//! The erased path with a person on the end of it.
//!
//! Same three calls as [`crate::frontend::cli`]: [`wire::available`] for the menu,
//! [`form::of_schema`] for the arguments, [`wire::dispatch`] for the write.
//! Nothing here names an action key — a row appears in a menu because the
//! action is registered, and its form has the fields its payload type derived.
//! Adding a fifth issue action changes no line of this file.
//!
//! Every screen is one movable list of rows, and a row is either somewhere to
//! go or something to do. That is what lets three screens share one cursor, one
//! Enter and one renderer, and it is the shape the action framework suggests
//! rather than one imposed on it: the children of an object and the actions
//! over it are both just things you can pick.
//!
//! Nothing is cached across a write. Every write reloads what it drew, so a
//! menu is never left showing a row that has since changed, and the dispatch
//! that follows a submission is checked again against the row as it is now.
//!
//! # Why this is three functions and not one
//!
//! nottui is incremental and ratatui is immediate-mode, so the two frontends
//! cannot have the same shape. What is kept is the property that matters: the
//! view is a value, not a session.
//!
//! [`render`] and [`on_key`] are pure and [`apply`] is where the database is.
//! OCaml can call `Wire.dispatch` inline from its key handler because its state
//! holds the connection and Lwt is ambient; Rust would have to make `on_key`
//! `async` to do the same, and an async key handler is not testable without a
//! runtime driving it. [`Intent`] is the seam, and `tests/frontend.rs` asserts
//! against it directly.

use ratatui::Frame;
use ratatui::layout::{Constraint, Layout};
use ratatui::style::{Modifier, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::Value;

use crate::domains::issue::actions as issue_actions;
use crate::domains::issue::services as issue_services;
use crate::domains::project::actions as project_actions;
use crate::domains::project::services as project_services;
use crate::platform::action::Offered;
use crate::platform::db::{self, Db};
use crate::platform::error::Error;
use crate::platform::form::{self, Kind};
use crate::platform::wire::{self, Entry};

pub const BROWSING: &str = "up/down to move, enter to pick, esc to go back, q to quit";
pub const FILLING: &str = "tab to move, left/right to choose, enter to submit, esc to go back";

/// Where the cursor is. `Detail` carries the slug as well as the id because the
/// way back out of a deleted issue is the project it was in, and by then the
/// issue is not there to be asked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Screen {
    Projects,
    Issues(String),
    Detail { project: String, id: i64 },
}

impl Screen {
    fn parent(&self) -> Option<Screen> {
        match self {
            Screen::Projects => None,
            Screen::Issues(_) => Some(Screen::Projects),
            Screen::Detail { project, .. } => Some(Screen::Issues(project.clone())),
        }
    }
}

/// A row is either somewhere to go or something to do. A refused `Do` stays in
/// the list with its reason rather than being dropped, which is the whole of
/// what availability buys a person over a fixed set of commands.
///
/// A `Do` carries the key it would run and nothing else — no object, no closure
/// and no tag for which store call to make. Each screen is about one object,
/// and that object is the whole of what determines its group, so [`write`]
/// loads the row again and dispatches against the group the screen already
/// implies. What each action then writes is the action's own business.
#[derive(Debug, Clone)]
pub enum Row {
    Go {
        label: String,
        to: Screen,
    },
    Do {
        key: String,
        offered: Offered,
        schema: Value,
    },
}

impl Row {
    pub fn label(&self) -> String {
        match self {
            Row::Go { label, .. } => label.clone(),
            Row::Do {
                key,
                offered: Offered::Runnable,
                ..
            } => key.clone(),
            Row::Do {
                key,
                offered: Offered::Refused(reason),
                ..
            } => format!("{key} ({reason})"),
        }
    }
}

/// A form control. A text box for a string, a cycling selector for an enum —
/// the first control the schema has ever asked a frontend here to derive that
/// is not an edit field.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Control {
    Editing(String),
    Choosing { values: Vec<String>, index: usize },
}

impl Control {
    pub fn value(&self) -> String {
        match self {
            Control::Editing(text) => text.clone(),
            Control::Choosing { values, index } => values.get(*index).cloned().unwrap_or_default(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entered {
    pub field: form::Field,
    pub control: Control,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Form {
    pub key: String,
    pub entries: Vec<Entered>,
    pub focus: usize,
}

#[derive(Debug, Clone)]
pub struct State {
    pub screen: Screen,
    pub header: String,
    pub rows: Vec<Row>,
    pub selected: usize,
    /// `Some` while a form is up. The screen underneath does not change, so
    /// leaving a form is dropping this and nothing else.
    pub form: Option<Form>,
    pub status: String,
    pub quit: bool,
}

impl State {
    fn empty() -> State {
        State {
            screen: Screen::Projects,
            header: String::new(),
            rows: Vec::new(),
            selected: 0,
            form: None,
            status: BROWSING.to_string(),
            quit: false,
        }
    }

    pub fn selected_row(&self) -> Option<&Row> {
        self.rows.get(self.selected)
    }
}

/// What a keystroke means. Nothing about it touches a database, which is what
/// makes [`on_key`] assertable without a runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Intent {
    Ignored,
    Move(i32),
    Enter,
    Back,
    Quit,
    Insert(char),
    Rub,
    NextField,
    Cycle(i32),
    Submit,
}

// --- rows -----------------------------------------------------------------

fn action_rows<O>(group: &[Entry<O>], obj: &O) -> Vec<Row> {
    wire::available(group, obj)
        .into_iter()
        .map(|(entry, offered)| Row::Do {
            key: entry.key.to_string(),
            offered,
            schema: entry.schema.clone(),
        })
        .collect()
}

/// One screen, read fresh. The header is what the screen is about and the rows
/// are what can be done from it, in that order.
pub async fn load(db: &Db, screen: &Screen) -> Result<(String, Vec<Row>), Error> {
    match screen {
        Screen::Projects => {
            let projects = project_services::projects(db).await?;
            let mut rows = action_rows(&project_actions::root(), &projects);
            rows.extend(projects.iter().map(|p| Row::Go {
                label: format!(
                    "{:<12} {:<24} {:<8} {}/{}/{}",
                    p.slug,
                    p.title,
                    wire::name_of(&p.status),
                    p.todo,
                    p.doing,
                    p.done
                ),
                to: Screen::Issues(p.slug.clone()),
            }));
            Ok(("projects".to_string(), rows))
        }
        Screen::Issues(slug) => {
            let project = project_services::project(db, slug)
                .await?
                .ok_or_else(|| Error::Invalid(format!("no project {slug:?}")))?;
            let issues = issue_services::issues(db, slug).await?;
            let mut rows = action_rows(&project_actions::group(), &project);
            rows.extend(issues.iter().map(|i| Row::Go {
                label: format!(
                    "{:<4} {:<8} {:<6} {}",
                    i.id,
                    wire::name_of(&i.status),
                    wire::name_of(&i.priority),
                    i.title
                ),
                to: Screen::Detail {
                    project: slug.clone(),
                    id: i.id,
                },
            }));
            Ok((format!("{}: {}", project.subject(), project.title), rows))
        }
        Screen::Detail { id, .. } => {
            let issue = issue_services::issue(db, *id)
                .await?
                .ok_or_else(|| Error::Invalid(format!("no issue {id}")))?;
            let rows = action_rows(&issue_actions::group(), &issue);
            Ok((format!("{}: {}", issue.subject(), issue.title), rows))
        }
    }
}

// --- the write ------------------------------------------------------------

/// Load, then dispatch, inside one transaction. The store call each action ends
/// in is the action's own, done by its `execute`; each screen is about one
/// object, and that object is the whole of what determines its group — so there
/// is no group-by-group pairing left to write here, only which object the
/// screen loads. A refusal rolls the call back.
async fn write(db: &Db, screen: &Screen, key: &str, payload: &Value) -> Result<String, Error> {
    match screen {
        Screen::Projects => {
            db::transaction(db, async |tx| {
                let projects = project_services::projects(tx).await?;
                wire::dispatch(&project_actions::root(), projects, key, payload, tx).await
            })
            .await
        }
        Screen::Issues(slug) => {
            db::transaction(db, async |tx| {
                let project = project_services::project(tx, slug)
                    .await?
                    .ok_or_else(|| Error::Invalid(format!("no project {slug:?}")))?;
                wire::dispatch(&project_actions::group(), project, key, payload, tx).await
            })
            .await
        }
        Screen::Detail { id, .. } => {
            db::transaction(db, async |tx| {
                let issue = issue_services::issue(tx, *id)
                    .await?
                    .ok_or_else(|| Error::Invalid(format!("no issue {id}")))?;
                wire::dispatch(&issue_actions::group(), issue, key, payload, tx).await
            })
            .await
        }
    }
}

// --- keys -----------------------------------------------------------------

/// Pure. A form takes the keys a form takes and the list takes the rest, and
/// nothing here decides what a key *does* — only what it means.
pub fn on_key(state: &State, key: crossterm::event::KeyCode) -> Intent {
    use crossterm::event::KeyCode::*;
    match (&state.form, key) {
        (Some(_), Esc) => Intent::Back,
        (Some(_), Tab | Down) => Intent::NextField,
        (Some(_), Enter) => Intent::Submit,
        (Some(_), Backspace) => Intent::Rub,
        (Some(_), Left) => Intent::Cycle(-1),
        (Some(_), Right) => Intent::Cycle(1),
        (Some(_), Char(c)) => Intent::Insert(c),
        (None, Up) => Intent::Move(-1),
        (None, Down) => Intent::Move(1),
        (None, Enter) => Intent::Enter,
        (None, Esc) => Intent::Back,
        (None, Char('q')) => Intent::Quit,
        _ => Intent::Ignored,
    }
}

fn control_of(field: &form::Field) -> Control {
    match &field.kind {
        Kind::Text | Kind::OptionalText => Control::Editing(String::new()),
        Kind::Enum(values) => Control::Choosing {
            values: values.clone(),
            index: 0,
        },
    }
}

/// The first screen, loaded.
pub async fn start(db: &Db) -> State {
    reload(db, State::empty()).await
}

/// Read the current screen back, and fall out to the parent if the object it is
/// about has gone. That is how deleting the thing you are looking at navigates,
/// and it names no action key to do it.
async fn reload(db: &Db, mut state: State) -> State {
    match load(db, &state.screen).await {
        Ok((header, rows)) => {
            state.selected = state.selected.min(rows.len().saturating_sub(1));
            state.header = header;
            state.rows = rows;
            state
        }
        Err(e) => match state.screen.parent() {
            None => {
                state.status = e.to_string();
                state.rows = Vec::new();
                state
            }
            Some(parent) => {
                state.screen = parent;
                state.selected = 0;
                Box::pin(reload(db, state)).await
            }
        },
    }
}

/// The half with the database in it.
pub async fn apply(db: &Db, mut state: State, intent: Intent) -> State {
    match intent {
        Intent::Ignored => state,
        Intent::Quit => {
            state.quit = true;
            state
        }
        Intent::Move(n) => {
            let last = state.rows.len().saturating_sub(1);
            state.selected = (state.selected as i64 + n as i64).clamp(0, last as i64) as usize;
            state
        }
        Intent::Back => {
            if state.form.is_some() {
                state.form = None;
                state.status = BROWSING.to_string();
                return state;
            }
            match state.screen.parent() {
                None => state,
                Some(parent) => {
                    state.screen = parent;
                    state.selected = 0;
                    reload(db, state).await
                }
            }
        }
        Intent::Enter => match state.selected_row().cloned() {
            None => state,
            Some(Row::Go { to, .. }) => {
                state.screen = to;
                state.selected = 0;
                state.status = BROWSING.to_string();
                reload(db, state).await
            }
            Some(Row::Do {
                key,
                offered: Offered::Refused(reason),
                ..
            }) => {
                state.status = format!("{key}: {reason}");
                state
            }
            Some(Row::Do { key, schema, .. }) => {
                // A schema the form cannot render is reported rather than
                // approximated, so an action is never handed a payload missing
                // half of what it asked for.
                match form::of_schema(&schema) {
                    Err(message) => {
                        state.status = format!("{key}: {message}");
                        state
                    }
                    Ok(fields) => {
                        state.form = Some(Form {
                            key,
                            entries: fields
                                .into_iter()
                                .map(|field| Entered {
                                    control: control_of(&field),
                                    field,
                                })
                                .collect(),
                            focus: 0,
                        });
                        state.status = FILLING.to_string();
                        state
                    }
                }
            }
        },
        Intent::NextField => {
            if let Some(form) = state.form.as_mut()
                && !form.entries.is_empty()
            {
                form.focus = (form.focus + 1) % form.entries.len();
            }
            state
        }
        Intent::Insert(c) => {
            if let Some(entry) = focused(&mut state)
                && let Control::Editing(text) = &mut entry.control
            {
                text.push(c);
            }
            state
        }
        Intent::Rub => {
            if let Some(entry) = focused(&mut state)
                && let Control::Editing(text) = &mut entry.control
            {
                text.pop();
            }
            state
        }
        Intent::Cycle(n) => {
            if let Some(entry) = focused(&mut state)
                && let Control::Choosing { values, index } = &mut entry.control
                && !values.is_empty()
            {
                let len = values.len() as i64;
                *index = ((*index as i64 + n as i64).rem_euclid(len)) as usize;
            }
            state
        }
        Intent::Submit => {
            let Some(form) = state.form.clone() else {
                return state;
            };
            let values: Vec<(form::Field, String)> = form
                .entries
                .iter()
                .map(|e| (e.field.clone(), e.control.value()))
                .collect();
            let payload = form::payload(&values);
            match write(db, &state.screen, &form.key, &payload).await {
                // A refusal leaves the form up with its values intact, because
                // that is where the fix usually is.
                Err(e) => {
                    state.status = format!("{}: {e}", form.key);
                    state
                }
                Ok(message) => {
                    state.form = None;
                    state.status = message;
                    reload(db, state).await
                }
            }
        }
    }
}

fn focused(state: &mut State) -> Option<&mut Entered> {
    let form = state.form.as_mut()?;
    let focus = form.focus;
    form.entries.get_mut(focus)
}

// --- rendering ------------------------------------------------------------

/// Pure, in the sense that matters: it reads the state and touches nothing
/// else, so `tests/frontend.rs` renders into a `TestBackend` buffer and asserts
/// against what came out.
pub fn render(state: &State, frame: &mut Frame) {
    let areas = Layout::vertical([
        Constraint::Length(1),
        Constraint::Length(1),
        Constraint::Min(1),
        Constraint::Length(1),
        Constraint::Length(1),
    ])
    .split(frame.area());

    frame.render_widget(
        Paragraph::new(state.header.as_str()).style(Style::new().add_modifier(Modifier::BOLD)),
        areas[0],
    );

    let body: Vec<Line> = match &state.form {
        None => state
            .rows
            .iter()
            .enumerate()
            .map(|(i, row)| {
                let pointer = if i == state.selected { "> " } else { "  " };
                let line = Line::from(format!("{pointer}{}", row.label()));
                match row {
                    Row::Do {
                        offered: Offered::Refused(_),
                        ..
                    } => line.dim(),
                    _ => line,
                }
            })
            .collect(),
        Some(form) => {
            let mut lines = vec![Line::from(
                Span::from(form.key.clone()).add_modifier(Modifier::BOLD),
            )];
            lines.extend(form.entries.iter().enumerate().map(|(i, entry)| {
                let pointer = if i == form.focus { "> " } else { "  " };
                // The label is the field's description, which `schemars` took
                // from the payload type's doc comment. The OCaml side has only
                // the property name to put here, because
                // `ppx_deriving_jsonschema` emits nothing else — this is
                // finding 2 with a person looking at it.
                let label = entry
                    .field
                    .description
                    .clone()
                    .unwrap_or_else(|| entry.field.name.clone());
                let mark = if entry.field.required { "*" } else { "" };
                Line::from(format!(
                    "{pointer}{}{mark} [{}]  {label}",
                    entry.field.name,
                    entry.control.value()
                ))
            }));
            lines.push(Line::from("* required").dim());
            lines
        }
    };
    frame.render_widget(Paragraph::new(body), areas[2]);
    frame.render_widget(Paragraph::new(state.status.as_str()).dim(), areas[4]);
}
