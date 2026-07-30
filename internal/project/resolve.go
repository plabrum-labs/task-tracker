// Package project owns the container an issue lands in: inferring which
// project the user means from where they are standing, and creating it on
// first use.
package project

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// MarkerFile is the per-directory override. Its first meaningful line is the
// project slug.
const MarkerFile = ".tt"

// EnvVar overrides inference for a whole shell session.
const EnvVar = "TT_PROJECT"

// Source records which rule produced a slug. It exists so an error can say why
// an issue was about to land where it did — "created in `dotfiles` (from
// ../.git)" is actionable in a way that "created in `dotfiles`" is not.
type Source string

const (
	SourceOverride Source = "override" // --project
	SourceEnv      Source = "env"      // $TT_PROJECT
	SourceMarker   Source = "marker"   // a .tt file
	SourceGit      Source = "git"      // nearest ancestor .git
	SourceDir      Source = "dir"      // basename of the working directory
)

// Scope is a resolved project reference.
type Scope struct {
	Slug   string
	Source Source
}

// Resolver turns a working directory into a project slug.
//
// Both ambient inputs are fields rather than direct calls to os.Getwd and
// os.Getenv. That is what makes resolution testable: a test builds a tree under
// t.TempDir, points Dir at a leaf, and the real filesystem does the rest. No
// test ever has to chdir, so they all stay parallel-safe.
type Resolver struct {
	Dir    string
	Getenv func(string) string
}

// NewResolver builds a Resolver over the real process environment.
func NewResolver() (Resolver, error) {
	dir, err := os.Getwd()
	if err != nil {
		return Resolver{}, fmt.Errorf("determining working directory: %w", err)
	}
	return Resolver{Dir: dir, Getenv: os.Getenv}, nil
}

// Resolve applies DESIGN.md's resolution order: override, $TT_PROJECT, the
// nearest .tt file, the nearest ancestor .git, then the working directory's
// own name.
//
// Rules 3 and 4 share one upward walk rather than two, because they walk the
// same chain: return on the first .tt, and remember the first .git in case no
// marker ever turns up.
func (r Resolver) Resolve(override string) (Scope, error) {
	if s := strings.TrimSpace(override); s != "" {
		return r.scope(s, SourceOverride, "--project")
	}
	if r.Getenv != nil {
		if s := strings.TrimSpace(r.Getenv(EnvVar)); s != "" {
			return r.scope(s, SourceEnv, "$"+EnvVar)
		}
	}

	var gitRoot string
	for dir := r.Dir; ; {
		if name, ok := readMarker(filepath.Join(dir, MarkerFile)); ok {
			return r.scope(name, SourceMarker, filepath.Join(dir, MarkerFile))
		}
		// First .git wins — the walk runs leaf-upward, so first is nearest.
		if gitRoot == "" && isRepoRoot(dir) {
			gitRoot = dir
		}
		parent := filepath.Dir(dir)
		// filepath.Dir is its own fixed point at the root of the volume. That
		// is the only portable stop condition; "== /" is wrong on Windows and
		// on a Unix path that never had a leading slash.
		if parent == dir {
			break
		}
		dir = parent
	}

	if gitRoot != "" {
		return r.scope(filepath.Base(gitRoot), SourceGit, gitRoot)
	}
	return r.scope(filepath.Base(r.Dir), SourceDir, r.Dir)
}

// scope slugifies and rejects an input that slugifies away to nothing.
//
// Everything funnels through here so `--project My-Repo`, TT_PROJECT="my repo"
// and a directory called "My Repo" cannot produce three separate rows for one
// project.
func (r Resolver) scope(raw string, src Source, origin string) (Scope, error) {
	slug := Slugify(raw)
	if slug == "" {
		return Scope{}, fmt.Errorf(
			"cannot infer a project name from %s (%q): pass --project", origin, raw)
	}
	return Scope{Slug: slug, Source: src}, nil
}

// Slugify normalises a name to lowercase [a-z0-9._-], collapsing every run of
// other characters into a single dash and trimming dashes from the ends.
func Slugify(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	dash := false
	for _, r := range strings.ToLower(strings.TrimSpace(s)) {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9', r == '.', r == '_', r == '-':
			if dash && b.Len() > 0 {
				b.WriteByte('-')
			}
			dash = false
			b.WriteRune(r)
		default:
			// Deferred: a run of junk becomes one dash, and a trailing run
			// becomes none, without a second pass to trim.
			dash = true
		}
	}
	return strings.Trim(b.String(), "-")
}

// readMarker reads the project name out of a .tt file, reporting whether one
// was found.
//
// Every failure — missing, unreadable, a directory, present but empty —
// reports "not found" rather than an error. A stray empty marker should let
// inference carry on to the git rule, not abort the command or, worse, create
// a project named "".
//
// Only the first meaningful line is read, and # comments are skipped, so the
// file can grow into a small config later without an older binary choking on
// it.
func readMarker(path string) (string, bool) {
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return "", false
	}
	f, err := os.Open(path)
	if err != nil {
		return "", false
	}
	defer f.Close()

	scan := bufio.NewScanner(f)
	for scan.Scan() {
		line := strings.TrimSpace(scan.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		return line, true
	}
	return "", false
}

// isRepoRoot reports whether dir holds a .git. A .git *file* counts: that is
// how a linked worktree and a submodule point at their real git directory, and
// both are places you would expect `tt` to work.
func isRepoRoot(dir string) bool {
	_, err := os.Stat(filepath.Join(dir, ".git"))
	return err == nil
}
