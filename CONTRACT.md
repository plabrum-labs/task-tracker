# tt — contract

`backend` is what `cli/` and `tui/` call. `backend/models` is what it returns.

## Layout

```
backend/                   the backend
  contract.go              package backend — API, Open, every method a frontend calls
  models/                  public: the contract types. Imports no ent.
    action.go              Action[K], Rollup
    issues.go              Issue, IssueStatus, Priority, IssueKey, params
    projects.go            Project, ProjectStatus, ProjectKey, params
    comments.go            Comment, CommentKey
    taxonomy.go            Milestone, Label
  errs/                    public: the four sentinels
  internal/                private: cli/ and tui/ cannot import any of this
    issues/                queries.go  actions.go  service.go
    projects/
    comments/
    milestones/            queries.go  service.go — not an object, no actions.go
    labels/
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
models          imports nothing
  ↑        ↑
domains    contract.go
```

`models` is one package rather than one per domain because `Issue` holds a `Project` and
`[]Comment`. Distributed models stay acyclic only while the nesting happens to point one
way; one package makes a cycle structurally impossible however the graph grows.

Frontends import `backend`, `backend/models` and `backend/errs` — three ent-free
packages. Everything else lives under `backend/internal/`, so `cli` and `tui` **cannot**
import a domain package, `db`, or `ent`. The compiler rejects it, and `API.client` is
unexported so there is no client to be had.

`models` and `errs` are public members of the backend rather than peers of it. They are
pure data with no path to a database, so leaving them importable costs nothing and saves
a file of aliases whose only job would be to tunnel types through `internal/`.

There is no root `internal/`. Within a single module it enforces nothing; the only barrier
that does any work is `backend/internal/`.

Ent lives at `backend/internal/ent` rather than the repository root: it is the backend's
persistence detail, not a peer of the frontends. `migrations/` stays at the root — it is
an ops artifact read by the Atlas CLI, not a Go dependency.

Ent types are not the contract: a contract object carries its own `Actions`, and an Ent
entity cannot.

## `backend/models`

### action.go

```go
// Action is one entry in an object's menu.
//
// Absent from the slice means the action does not apply here at all. Present with
// Reason == "" means it can run now. Present with a Reason means it applies but is
// refused, and Reason says why.
type Action[K ~string] struct {
	Key       K      `json:"key"`
	Label     string `json:"label"`
	Reason    string `json:"reason"`    // "" when runnable
	Forceable bool   `json:"forceable"` // refusal is overridable with --force
}

type Rollup struct {
	Done  int `json:"done"`
	Total int `json:"total"`
}
```

Each object has its own `Key` type so the type and its constants sit in one package and
`exhaustive` can check a frontend's dispatch switch.

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

	Parents  []Issue `json:"parents"`  // this issue is a subtask of these
	Subtasks []Issue `json:"subtasks"` // ref kind = subtask
	Blockers []Issue `json:"blockers"` // ref kind = dep, must close before this starts
	Blocks   []Issue `json:"blocks"`   // ref kind = dep, reverse edge

	Comments []Comment `json:"comments"` // oldest first

	Blocked bool   `json:"blocked"` // any Blockers not done — the ⊘ badge
	Rollup  Rollup `json:"rollup"`  // over Subtasks — the 3/5 badge

	Actions []Action[IssueKey] `json:"-"`
}

type IssueListParams struct {
	Project     string        // "" means every project
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
	SubOf     *int
}

type IssueEditParams struct {
	Title *string // nil leaves unchanged
	Body  *string
}
```

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

	Rollup Rollup `json:"rollup"` // over the project's issues

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

### comments.go

```go
type CommentKey string

const (
	KeyCommentEdit   CommentKey = "edit"
	KeyCommentDelete CommentKey = "delete"
)

type Comment struct {
	ID       int        `json:"id"`
	At       time.Time  `json:"at"`
	EditedAt *time.Time `json:"edited_at"` // nil when never edited
	Author   string     `json:"author"`
	Body     string     `json:"body"`

	Actions []Action[CommentKey] `json:"-"`
}
```

No list or show method. A list of comments is only ever the comments on one issue, and
`ShowIssue` already returns them.

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
func (a *API) Close() error

// Reads
func (a *API) ListIssues(ctx context.Context, p models.IssueListParams) ([]models.Issue, error)
func (a *API) ShowIssue(ctx context.Context, id int) (models.Issue, error)
func (a *API) ListProjects(ctx context.Context, p models.ProjectListParams) ([]models.Project, error)
func (a *API) ShowProject(ctx context.Context, slug string) (models.Project, error)

// Writes — one per Key, plus Add, which has no subject and so no Key.
func (a *API) Add(ctx context.Context, p models.IssueAddParams) (models.Issue, error)

func (a *API) IssueStart(ctx context.Context, id int) (models.Issue, error)
func (a *API) IssueClose(ctx context.Context, id int, force bool) (models.Issue, error)
func (a *API) IssueReopen(ctx context.Context, id int) (models.Issue, error)
func (a *API) IssueEdit(ctx context.Context, id int, p models.IssueEditParams) (models.Issue, error)
func (a *API) IssueDelete(ctx context.Context, id int) error
func (a *API) IssueSetPriority(ctx context.Context, id int, p models.Priority) (models.Issue, error)
func (a *API) IssueSetMilestone(ctx context.Context, id int, name string) (models.Issue, error)
func (a *API) IssueAddLabel(ctx context.Context, id int, name string) (models.Issue, error)
func (a *API) IssueRemoveLabel(ctx context.Context, id int, name string) (models.Issue, error)
func (a *API) IssueAddSubIssue(ctx context.Context, id int, p models.IssueAddParams) (models.Issue, error)
func (a *API) IssueAddDep(ctx context.Context, id, blockerID int) (models.Issue, error)
func (a *API) IssueRemoveDep(ctx context.Context, id, blockerID int) (models.Issue, error)
func (a *API) IssueComment(ctx context.Context, id int, body string) (models.Issue, error)

func (a *API) ProjectEdit(ctx context.Context, slug string, p models.ProjectEditParams) (models.Project, error)
func (a *API) ProjectArchive(ctx context.Context, slug string) (models.Project, error)
func (a *API) ProjectRestore(ctx context.Context, slug string) (models.Project, error)

func (a *API) CommentEdit(ctx context.Context, id int, body string) (models.Comment, error)
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
func Load(ctx context.Context, cl *ent.Client, id int) (models.Issue, error)
func List(ctx context.Context, cl *ent.Client, p models.IssueListParams) ([]models.Issue, error)

// actions.go — pure. No context, no client, no error, no queries.
func Actions(i models.Issue) []models.Action[models.IssueKey]

// service.go — writes. Paired: the plain one owns the transaction via db.WithTx,
// the …In one composes into a caller's.
func Close(ctx context.Context, cl *ent.Client, id int, force bool) (models.Issue, error)
func CloseIn(ctx context.Context, tx *ent.Client, id int, force bool) (models.Issue, error)
```

Domains may import sibling domains — `issues.Add` calls `projects.EnsureIn` inside its own
transaction. `queries.go` replaces the `query/` package: a list read that joins labels and
refs is the issues domain's read.

## Depth

The graph is recursive, so loading stops one level down.

A top-level `Issue` has everything populated. The `Issue` values inside `Parents`,
`Subtasks`, `Blockers` and `Blocks` have their **scalars, `Project`, `Labels`, `Blocked`,
`Rollup` and `Actions`** populated, and their own `Parents`, `Subtasks`, `Blockers`,
`Blocks` and `Comments` empty.

A rule may read one level down, never two. Anything needing a deeper walk — the cycle
check on `IssueAddDep` — is not a menu rule and belongs in `service.go`.

Every slice is non-nil. Empty means empty.

## Rules

`Actions` is computed in `actions.go` from the loaded object and nothing else. Every fact
a rule reads is a field on the object, which is what the one-level depth guarantee is for.

The same rule is checked twice: once to build `Actions`, once in `service.go` against live
rows. The menu is a snapshot; the write enforces. Each Key needs both tests.

Presentation is the frontend's: which keystroke, what a prompt asks for, menu order. The
frontend switches on the `Key` with no `default` arm so `exhaustive` fails the lint when a
Key is added and not handled.

`Actions` is `json:"-"`. The CLI does not render menus; `--json` is unchanged by this.

Only `Issue` and `Project` have a status. Transition topology is an exhaustive switch on
the current value, not a map. All transitions are user-initiated — no system or cascade
edges. No transition log.

## Open

- `ProjectStatus` values. `active`/`archived` assumed above. Whether `paused`, or a `done`
  distinct from archived, is unsettled.
- Whether an archived project's issues drop out of `ListIssues` by default, and what
  `projects.Ensure` does when capture targets an archived project.
- `Parents []Issue` — the schema permits an issue to be a subtask of several. If a
  uniqueness rule is wanted, this becomes `Parent *Issue`.
- `Comment.EditedAt` assumes edits are traceable and deletes are hard. Both are schema
  changes.
- `Forceable` exists for `IssueClose` against open subtasks, per `IMPLEMENTATION.md`. If no
  other action needs an override, it may not deserve a field on every `Action`.
- Whether `KeyIssueRemoveDep` and `KeyIssueRemoveLabel` appear once on the issue with the
  frontend prompting, or once per blocker/label row. The types above assume the former.
- Moving ent to `backend/internal/ent` changes the `entc` target path, the `ent://` source
  in `atlas.hcl`, and every reference to `ent/schema/` in `DESIGN.md` and
  `IMPLEMENTATION.md`. Mechanical, but it lands in the same change as the tree move.
