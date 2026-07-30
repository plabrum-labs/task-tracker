# tt — contract

`backend` is what `cli/` and `tui/` call. `backend/contract` is what it returns.

## Layout

```
backend/                   the backend
  contract.go              package backend — API, Open, every method a frontend calls
  atlas.hcl                the `local` env: ent:// source, dev database, dir
  contract/                public: the contract types. Imports no ent.
    action.go              Action[K]
    issues.go              Issue, IssueStatus, Priority, IssueKey, params, parsers
    projects.go            Project, ProjectStatus, ProjectKey, params, parser
    comments.go            Comment, CommentKey
    taxonomy.go            Milestone, Label
  errs/                    public: the four sentinels
  internal/                private: cli/ and tui/ cannot import any of this
    issues/                queries.go  actions.go  service.go
    projects/              queries.go  actions.go  service.go  resolve.go
    comments/              queries.go  actions.go  service.go
    milestones/            queries.go  service.go — not an object, no actions.go
    labels/                queries.go  service.go — not an object, no actions.go
    db/                    open, pragmas, WithTx
    dbtest/
    ent/                   schema/ + generated client

migrations/
cli/
tui/
main.go
```

Import direction:

```
contract        imports nothing but errs
  ↑        ↑
domains    contract.go
```

`contract` is one package rather than one per domain because `Issue` holds a `Project` and
`[]Comment`. Split per domain, the types stay acyclic only while the nesting happens to
point one way; one package makes a cycle structurally impossible however the graph grows.

Frontends import `backend`, `backend/contract` and `backend/errs` — three ent-free
packages. Everything else lives under `backend/internal/`, so `cli` and `tui` **cannot**
import a domain package, `db`, or `ent`. The compiler rejects it, and `API.client` is
unexported so there is no client to be had.

`contract` and `errs` are public members of the backend rather than peers of it. They are
pure data with no path to a database, so leaving them importable costs nothing and saves
a file of aliases whose only job would be to tunnel types through `internal/`.

There is no root `internal/`. Within a single module it enforces nothing; the only barrier
that does any work is `backend/internal/`.

Ent lives at `backend/internal/ent` rather than the repository root: it is the backend's
persistence detail, not a peer of the frontends. `migrations/` stays at the root — it is
an ops artifact read by the Atlas CLI, not a Go dependency.

`atlas.hcl` sits in `backend/`, and the `db-*` recipes `cd` there before running Atlas.
They have to: Atlas's ent loader writes a temporary package into `.entc/` in the working
directory, and from the repository root that package cannot import
`backend/internal/ent/schema`. Its paths are therefore relative to `backend/` —
`ent://internal/ent/schema`, `file://../migrations`.

Ent types are not the contract: a contract object carries its own `Actions`, and an Ent
entity cannot.

## `backend/contract`

### action.go

```go
// Action is one entry in an object's menu.
//
// Absent from the slice means the action does not apply here at all. Present with
// Reason == "" means it can run now. Present with a Reason means it applies but is
// refused, and Reason says why.
type Action[K ~string] struct {
	Key    K      `json:"key"`
	Label  string `json:"label"`
	Reason string `json:"reason"` // "" when runnable
}

func (a Action[K]) Runnable() bool { return a.Reason == "" }
```

Each object has its own `Key` type so the type and its constants sit in one package and
`exhaustive` can check a frontend's dispatch switch.

There is no `Forceable`. An action either applies, or is refused with a reason; a refusal
the user can shrug off is not a rule worth having.

### issues.go

```go
type IssueStatus string

const (
	IssueTodo  IssueStatus = "todo"
	IssueDoing IssueStatus = "doing"
	IssueDone  IssueStatus = "done"
)

type Priority string

const (
	PriorityNormal Priority = "normal"
	PriorityHi     Priority = "hi"
)

type IssueKey string

const (
	KeyIssueStart        IssueKey = "start"
	KeyIssueClose        IssueKey = "close"
	KeyIssueReopen       IssueKey = "reopen"
	KeyIssueEdit         IssueKey = "edit"
	KeyIssueDelete       IssueKey = "delete"
	KeyIssueSetPriority  IssueKey = "set-priority"
	KeyIssueSetMilestone IssueKey = "set-milestone"
	KeyIssueAddLabel     IssueKey = "add-label"
	KeyIssueRemoveLabel  IssueKey = "remove-label"
	KeyIssueAddSubIssue  IssueKey = "add-sub-issue"
	KeyIssueAddDep       IssueKey = "add-dep"
	KeyIssueRemoveDep    IssueKey = "remove-dep"
	KeyIssueComment      IssueKey = "comment"
)

type Issue struct {
	ID        int         `json:"id"`
	Title     string      `json:"title"`
	Body      string      `json:"body"`
	Status    IssueStatus `json:"status"`
	Priority  Priority    `json:"priority"`
	CreatedAt time.Time   `json:"created_at"`
	UpdatedAt time.Time   `json:"updated_at"`
	ClosedAt  *time.Time  `json:"closed_at"` // nil when open

	Project   Project    `json:"project"`
	Milestone *Milestone `json:"milestone"` // nil when none
	Labels    []Label    `json:"labels"`    // sorted by name

	Parent   *Issue  `json:"parent"`   // nil unless this is a sub-issue
	Subtasks []Issue `json:"subtasks"` // sub-issues of this one
	Blockers []Issue `json:"blockers"` // must close before this starts
	Blocks   []Issue `json:"blocks"`   // the reverse edge

	Comments []Comment `json:"comments"` // oldest first

	Blocked bool `json:"blocked"` // any blocker not done — the ⊘ badge

	Actions []Action[IssueKey] `json:"-"`
}

type IssueListParams struct {
	Project     string        // "" means every active project
	Statuses    []IssueStatus // empty means todo+doing
	Labels      []string      // ANDed
	Milestone   string
	Search      string // case-insensitive substring of title
	BlockedOnly bool
	Limit       int // 0 = no limit
}

type IssueAddParams struct {
	Project   string   // resolved slug, required
	Title     string   // required
	Body      string
	Priority  Priority // "" normalises to PriorityNormal
	Labels    []string
	Milestone string
}

type IssueEditParams struct {
	Title *string // nil leaves unchanged
	Body  *string
}
```

`Parent` is singular. A sub-issue has exactly one parent, and that is a plain nullable
`parent_id` column on `issues` rather than another kind of `Ref`: the column can hold one
value, so the rule needs no index and no service guard. Blocking stays many-to-many in
both directions and keeps `Ref` to itself.

`IssueAddParams` has no `SubOf`. Filing work under a parent is `IssueAddSubIssue`, so
there is one write that does it rather than two.

### projects.go

```go
type ProjectStatus string

const (
	ProjectActive   ProjectStatus = "active"
	ProjectArchived ProjectStatus = "archived"
)

type ProjectKey string

const (
	KeyProjectEdit    ProjectKey = "edit"
	KeyProjectArchive ProjectKey = "archive"
	KeyProjectRestore ProjectKey = "restore"
)

type Project struct {
	ID          int           `json:"id"`
	Slug        string        `json:"slug"`
	Title       string        `json:"title"`
	Description string        `json:"description"`
	Status      ProjectStatus `json:"status"`
	CreatedAt   time.Time     `json:"created_at"`
	UpdatedAt   time.Time     `json:"updated_at"`

	Actions []Action[ProjectKey] `json:"-"`
}

type ProjectListParams struct {
	Statuses []ProjectStatus // empty means active only
	Limit    int
}

type ProjectEditParams struct {
	Title       *string
	Description *string
}
```

A project does not carry its issues — that is `ListIssues` with `Project` set, which a
detail pane wants anyway for filtering and limits.

Archiving is what takes a project's work out of the way, so it does three things:
`ListProjects` hides it, `ListIssues` drops its issues unless `Project` names it, and
capture into it is refused until it is restored.

### comments.go

```go
type CommentKey string

const (
	KeyCommentEdit   CommentKey = "edit"
	KeyCommentDelete CommentKey = "delete"
)

type Comment struct {
	ID        int       `json:"id"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
	Author    string    `json:"author"`
	Body      string    `json:"body"`

	Actions []Action[CommentKey] `json:"-"`
}

func (c Comment) Edited() bool { return c.UpdatedAt.After(c.CreatedAt) }
```

No list or show method. A list of comments is only ever the comments on one issue, and
`ShowIssue` already returns them.

There is no `EditedAt`. Every entity carries `created_at`/`updated_at`, and an edited
comment is one whose two stamps have come apart.

> **Landmine.** `TimeMixin` defaults each stamp to `time.Now` separately, so a row written
> that way lands with `updated_at` a few microseconds after `created_at` — and `Edited()`
> would report every fresh comment as edited. `comments.AddIn` therefore sets both from one
> clock read. Deleting those two `Set…` calls does not fail a build or a query; it makes
> `Edited()` return true for everything.

### taxonomy.go

Not objects. No `Key`, no `Actions`.

```go
type Milestone struct {
	ID          int        `json:"id"`
	Name        string     `json:"name"`
	Description string     `json:"description"`
	Due         *time.Time `json:"due"` // nil when none
}

type Label struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}
```

## `backend/contract.go`

```go
type API struct {
	client *ent.Client
}

func Open(ctx context.Context, dsn string) (*API, error)
func New(client *ent.Client) *API
func DSN(path string) string
func ResolveProject(override string) (string, error)
func (a *API) Close() error

// Reads
func (a *API) ListIssues(ctx context.Context, p contract.IssueListParams) ([]contract.Issue, error)
func (a *API) ShowIssue(ctx context.Context, id int) (contract.Issue, error)
func (a *API) ListProjects(ctx context.Context, p contract.ProjectListParams) ([]contract.Project, error)
func (a *API) ShowProject(ctx context.Context, slug string) (contract.Project, error)
func (a *API) ListLabels(ctx context.Context) ([]contract.Label, error)
func (a *API) ListMilestones(ctx context.Context, slug string) ([]contract.Milestone, error)

// Writes — one per Key, plus Add, which has no subject and so no Key.
func (a *API) Add(ctx context.Context, p contract.IssueAddParams) (contract.Issue, error)

func (a *API) IssueStart(ctx context.Context, id int) (contract.Issue, error)
func (a *API) IssueClose(ctx context.Context, id int) (contract.Issue, error)
func (a *API) IssueReopen(ctx context.Context, id int) (contract.Issue, error)
func (a *API) IssueEdit(ctx context.Context, id int, p contract.IssueEditParams) (contract.Issue, error)
func (a *API) IssueDelete(ctx context.Context, id int) error
func (a *API) IssueSetPriority(ctx context.Context, id int, p contract.Priority) (contract.Issue, error)
func (a *API) IssueSetMilestone(ctx context.Context, id int, name string) (contract.Issue, error)
func (a *API) IssueAddLabel(ctx context.Context, id int, name string) (contract.Issue, error)
func (a *API) IssueRemoveLabel(ctx context.Context, id int, name string) (contract.Issue, error)
func (a *API) IssueAddSubIssue(ctx context.Context, id int, p contract.IssueAddParams) (contract.Issue, error)
func (a *API) IssueAddDep(ctx context.Context, id, blockerID int) (contract.Issue, error)
func (a *API) IssueRemoveDep(ctx context.Context, id, blockerID int) (contract.Issue, error)
func (a *API) IssueComment(ctx context.Context, id int, body string) (contract.Issue, error)

func (a *API) ProjectEdit(ctx context.Context, slug string, p contract.ProjectEditParams) (contract.Project, error)
func (a *API) ProjectArchive(ctx context.Context, slug string) (contract.Project, error)
func (a *API) ProjectRestore(ctx context.Context, slug string) (contract.Project, error)

func (a *API) CommentEdit(ctx context.Context, id int, body string) (contract.Comment, error)
func (a *API) CommentDelete(ctx context.Context, id int) error
```

Every method is one line: it hands `a.client` to a domain function. Composition and
transactions live in the domain that owns the write, through the `…In` pairing.

Writes return the object they mutated, reloaded through the same loader the reads use, so
a frontend never assembles state from what it sent.

`ShowIssue` on a missing id wraps `errs.ErrNotFound`. An action refused by a rule wraps
`errs.ErrConflict` with the same message its `Action.Reason` carries.

## `backend/internal/<domain>`

Three files, same shape in each.

```go
// queries.go — the only place that reads. One loader per object; every caller
// gets the same graph.
func Load(ctx context.Context, cl *ent.Client, id int) (contract.Issue, error)
func List(ctx context.Context, cl *ent.Client, p contract.IssueListParams) ([]contract.Issue, error)

// actions.go — pure. No context, no client, no error, no queries.
func Actions(e *ent.Issue) []contract.Action[contract.IssueKey]

// service.go — writes. Paired: the plain one owns the transaction via db.WithTx,
// the …In one composes into a caller's.
func Close(ctx context.Context, cl *ent.Client, id int) (contract.Issue, error)
func CloseIn(ctx context.Context, tx *ent.Client, id int) (contract.Issue, error)
```

Domains may import sibling domains — `issues.CreateIn` calls `projects.EnsureIn` inside its
own transaction. `queries.go` replaces the `query/` package: a list read that joins labels
and refs is the issues domain's read.

Taxonomy never depends on work: `projects`, `milestones` and `labels` do not import
`issues`. A read that needs a table the issues domain also uses reaches for the generated
`ent/issue` package, not the domain.

## Depth

The graph is recursive, so loading stops one level down.

A top-level `Issue` from `ShowIssue` has everything populated. The `Issue` values inside
`Parent`, `Subtasks`, `Blockers` and `Blocks` have their **scalars, `Project`,
`Milestone`, `Labels`, `Blocked` and `Actions`** populated, and their own `Parent`,
`Subtasks`, `Blockers`, `Blocks` and `Comments` empty. `ListIssues` returns rows at that
same depth.

A rule may read one level down, never two. Anything needing a deeper walk — the cycle
check on `IssueAddDep` — is not a menu rule and belongs in `service.go`.

Every slice is non-nil. Empty means empty.

## Rules

`Actions` takes the **ent row**, not the contract type. The loader eager-loads whichever
edges the rules read, so a menu is computed from real data at every depth rather than from
a contract object that may or may not have been filled in. Eager loading is batched — one
query per edge for a whole page, not one per row — so a list row carries a menu as correct
as a detail view's.

Which edges those are is decided by `actions.go` and supplied by `withMenuEdges` in
`queries.go`. That coupling is the one sharp edge here: an unloaded edge looks exactly like
an empty one, so dropping an edge from the loader does not fail a query, it silently
changes what the menu decides.

The same rule is checked twice: once to build `Actions`, once in `service.go` against live
rows. The menu is a snapshot; the write enforces. Each Key needs both tests. `service.go`
does not restate the rules — it calls `Actions` and turns a refusal into an
`errs.ErrConflict` carrying the same `Reason`, so the two cannot drift.

Presentation is the frontend's: which keystroke, what a prompt asks for, menu order. The
frontend switches on the `Key` with no `default` arm so `exhaustive` fails the lint when a
Key is added and not handled.

`Actions` is `json:"-"`. The CLI does not render menus; `--json` is unchanged by this.

Only `Issue` and `Project` have a status. Transition topology is an exhaustive switch on
the current value, not a map. All transitions are user-initiated — no system or cascade
edges. No transition log.

There are no derived count fields. A `3/5` badge is a rendering decision a frontend makes
from `Subtasks`, not a number the contract carries.

## Open

- Whether `ProjectStatus` wants a `paused`, or a `done` distinct from archived.
- Whether `KeyIssueRemoveDep` and `KeyIssueRemoveLabel` appear once on the issue with the
  frontend prompting, or once per blocker/label row. The types above assume the former.
- `DESIGN.md`, `IMPLEMENTATION.md` and `PHASES.md` still describe the superseded model:
  `Ref.kind`, subtask-as-a-kind-of-dependency, `--force`, rollups, and the
  `internal/app`+`internal/query` layout.
