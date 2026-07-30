// Package backend is the API cli/ and tui/ call. It returns the types in
// backend/contract and the sentinels in backend/errs, and nothing else.
//
// Every method here is one line into the domain that owns the object. There is
// no logic in this file: composition and transactions live in the domain,
// through the plain/…In pairing, so a rule cannot be reachable from one
// frontend and not the other.
package backend

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/comments"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
	"github.com/Plabrum/tt/backend/internal/issues"
	"github.com/Plabrum/tt/backend/internal/labels"
	"github.com/Plabrum/tt/backend/internal/milestones"
	"github.com/Plabrum/tt/backend/internal/projects"
)

// API is what cli/ and tui/ are handed.
//
// The ent client is unexported, and ent itself is under backend/internal, so a
// frontend cannot reach either. A frontend that could write a query would make
// the contract types stop being the contract: a schema change would start
// breaking the CLI directly instead of breaking the one method meant to absorb
// it.
type API struct {
	client *ent.Client
}

// Open connects to the store at dsn.
//
// The schema is not this function's business. A database that has never been
// migrated opens fine and fails on first query; bringing one up to date is
// `just db-upgrade`.
func Open(ctx context.Context, dsn string) (*API, error) {
	client, err := db.Open(ctx, dsn)
	if err != nil {
		return nil, err
	}
	return &API{client: client}, nil
}

// New returns an API over an existing client.
func New(client *ent.Client) *API {
	return &API{client: client}
}

// DSN builds the connection string for a database at path.
func DSN(path string) string {
	return db.DSN(path)
}

// Close releases the store.
func (a *API) Close() error {
	if err := a.client.Close(); err != nil {
		return fmt.Errorf("closing store: %w", err)
	}
	return nil
}

// Reads pass the client straight through rather than opening a transaction. The
// pool is pinned to one connection, so a BEGIN here would serialise the whole
// process behind itself and buy nothing in-process; the only exposure is a
// cross-process writer leaving a derived flag one refresh stale.

// ListIssues returns the issues matching p, in pick order.
func (a *API) ListIssues(ctx context.Context, p contract.IssueListParams) ([]contract.Issue, error) {
	return issues.List(ctx, a.client, p)
}

// ShowIssue returns one issue with everything hanging off it.
func (a *API) ShowIssue(ctx context.Context, id int) (contract.Issue, error) {
	return issues.Load(ctx, a.client, id)
}

// ListProjects returns the projects matching p.
func (a *API) ListProjects(ctx context.Context, p contract.ProjectListParams) ([]contract.Project, error) {
	return projects.List(ctx, a.client, p)
}

// ShowProject returns one project.
func (a *API) ShowProject(ctx context.Context, slug string) (contract.Project, error) {
	return projects.Load(ctx, a.client, slug)
}

// ListLabels returns every label.
func (a *API) ListLabels(ctx context.Context) ([]contract.Label, error) {
	return labels.List(ctx, a.client)
}

// ListMilestones returns one project's milestones.
func (a *API) ListMilestones(ctx context.Context, slug string) ([]contract.Milestone, error) {
	return milestones.ListForProject(ctx, a.client, slug)
}

// Writes: one per Key, plus Add, which has no subject and so no Key. Each
// returns the object it mutated, reloaded through the same loader the reads
// use, so a frontend never assembles state from what it sent.

// Add captures a new issue.
func (a *API) Add(ctx context.Context, p contract.IssueAddParams) (contract.Issue, error) {
	return issues.Create(ctx, a.client, p)
}

// IssueStart moves an issue into doing.
func (a *API) IssueStart(ctx context.Context, id int) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.Start, actions.None{})
}

// IssueClose marks an issue done.
func (a *API) IssueClose(ctx context.Context, id int) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.Close, actions.None{})
}

// IssueReopen brings a closed issue back to todo.
func (a *API) IssueReopen(ctx context.Context, id int) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.Reopen, actions.None{})
}

// IssueEdit changes an issue's title or body.
func (a *API) IssueEdit(ctx context.Context, id int, p contract.IssueEditParams) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.Edit, p)
}

// IssueDelete removes an issue.
func (a *API) IssueDelete(ctx context.Context, id int) error {
	_, err := issues.Run(ctx, a.client, id, issues.Delete, actions.None{})
	return err
}

// IssueSetPriority changes an issue's pick-order bump.
func (a *API) IssueSetPriority(ctx context.Context, id int, p contract.Priority) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.SetPriority, p)
}

// IssueSetMilestone files an issue under a milestone. An empty name clears it.
func (a *API) IssueSetMilestone(ctx context.Context, id int, name string) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.SetMilestone, name)
}

// IssueAddLabel puts a label on an issue.
func (a *API) IssueAddLabel(ctx context.Context, id int, name string) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.AddLabel, name)
}

// IssueRemoveLabel takes a label off an issue.
func (a *API) IssueRemoveLabel(ctx context.Context, id int, name string) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.RemoveLabel, name)
}

// IssueAddSubIssue captures a new issue as a child of this one.
func (a *API) IssueAddSubIssue(ctx context.Context, id int, p contract.IssueAddParams) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.AddSubIssue, p)
}

// IssueAddDep records that an issue waits on another.
func (a *API) IssueAddDep(ctx context.Context, id, blockerID int) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.AddDep, blockerID)
}

// IssueRemoveDep drops a dependency.
func (a *API) IssueRemoveDep(ctx context.Context, id, blockerID int) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.RemoveDep, blockerID)
}

// IssueComment appends a comment to an issue.
func (a *API) IssueComment(ctx context.Context, id int, body string) (contract.Issue, error) {
	return issues.Run(ctx, a.client, id, issues.Comment, body)
}

// ProjectEdit changes a project's title or description.
func (a *API) ProjectEdit(ctx context.Context, slug string, p contract.ProjectEditParams) (contract.Project, error) {
	return projects.Run(ctx, a.client, slug, projects.Edit, p)
}

// ProjectArchive takes a project's work out of the way.
func (a *API) ProjectArchive(ctx context.Context, slug string) (contract.Project, error) {
	return projects.Run(ctx, a.client, slug, projects.Archive, actions.None{})
}

// ProjectRestore brings an archived project back.
func (a *API) ProjectRestore(ctx context.Context, slug string) (contract.Project, error) {
	return projects.Run(ctx, a.client, slug, projects.Restore, actions.None{})
}

// CommentEdit replaces a comment's body.
func (a *API) CommentEdit(ctx context.Context, id int, body string) (contract.Comment, error) {
	return comments.Run(ctx, a.client, id, comments.Edit, body)
}

// CommentDelete removes a comment.
func (a *API) CommentDelete(ctx context.Context, id int) error {
	_, err := comments.Run(ctx, a.client, id, comments.Delete, actions.None{})
	return err
}

// ResolveProject turns a working directory into the project slug a capture
// lands in.
func ResolveProject(override string) (string, error) {
	r, err := projects.NewResolver()
	if err != nil {
		return "", err
	}
	scope, err := r.Resolve(override)
	if err != nil {
		return "", err
	}
	return scope.Slug, nil
}
