package projects

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fixture builds one tree that every resolution rule can be exercised against,
// and returns its root. Two repositories, because a `.tt` high in one subtree
// would otherwise shadow the fall-through cases in the other.
//
//	<base>/My Repo/.git/            a repo whose name needs slugifying
//	<base>/My Repo/sub/deep/        somewhere far below the root
//	<base>/My Repo/empty/.tt        present but says nothing
//	<base>/My Repo/dirmarker/.tt/   a directory wearing the marker's name
//	<base>/My Repo/inner/.git       a *file*, as a linked worktree has
//	<base>/nested/.git/             a second repo, with markers
//	<base>/nested/.tt               "outer-marker"
//	<base>/nested/marked/.tt        comments, a blank, then "from-marker"
//	<base>/nested/marked/child/
//	<base>/plain/                   no markers at all
//	<base>/Plain Two/               ditto, and needs slugifying
func fixture(t *testing.T) string {
	t.Helper()
	base := t.TempDir()

	mkdir := func(parts ...string) {
		t.Helper()
		p := filepath.Join(append([]string{base}, parts...)...)
		if err := os.MkdirAll(p, 0o755); err != nil {
			t.Fatalf("creating %s: %v", p, err)
		}
	}
	write := func(body string, parts ...string) {
		t.Helper()
		p := filepath.Join(append([]string{base}, parts...)...)
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatalf("writing %s: %v", p, err)
		}
	}

	mkdir("My Repo", ".git")
	mkdir("My Repo", "sub", "deep")
	mkdir("My Repo", "empty")
	write("\n# only a comment\n\n", "My Repo", "empty", ".tt")
	mkdir("My Repo", "dirmarker", ".tt")
	mkdir("My Repo", "inner")
	write("gitdir: /elsewhere/.git/worktrees/inner\n", "My Repo", "inner", ".git")

	mkdir("nested", ".git")
	write("outer-marker\n", "nested", ".tt")
	mkdir("nested", "marked", "child")
	write("# which project this is\n\n  from-marker  \nignored-second-line\n",
		"nested", "marked", ".tt")

	mkdir("plain")
	mkdir("Plain Two")

	return base
}

func TestResolveOrder(t *testing.T) {
	t.Parallel()
	base := fixture(t)

	tests := []struct {
		name     string
		dir      []string
		env      string
		override string
		want     string
		wantSrc  Source
	}{
		{
			name:     "override beats everything, and is slugified",
			dir:      []string{"nested", "marked"},
			env:      "from-env",
			override: "My-Repo",
			want:     "my-repo",
			wantSrc:  SourceOverride,
		},
		{
			name:    "env beats a marker",
			dir:     []string{"nested", "marked"},
			env:     "my repo",
			want:    "my-repo",
			wantSrc: SourceEnv,
		},
		{
			name:    "nearest marker wins, comments and blanks skipped",
			dir:     []string{"nested", "marked", "child"},
			want:    "from-marker",
			wantSrc: SourceMarker,
		},
		{
			name:    "an ancestor marker applies when there is no nearer one",
			dir:     []string{"nested"},
			want:    "outer-marker",
			wantSrc: SourceMarker,
		},
		{
			name:    "an empty marker falls through to git",
			dir:     []string{"My Repo", "empty"},
			want:    "my-repo",
			wantSrc: SourceGit,
		},
		{
			name:    "a directory named .tt is not a marker",
			dir:     []string{"My Repo", "dirmarker"},
			want:    "my-repo",
			wantSrc: SourceGit,
		},
		{
			name:    "git basename from far below the root",
			dir:     []string{"My Repo", "sub", "deep"},
			want:    "my-repo",
			wantSrc: SourceGit,
		},
		{
			name:    "nearest .git wins, and a .git file counts as a root",
			dir:     []string{"My Repo", "inner"},
			want:    "inner",
			wantSrc: SourceGit,
		},
		{
			name:    "cwd basename when nothing else applies",
			dir:     []string{"plain"},
			want:    "plain",
			wantSrc: SourceDir,
		},
		{
			name:    "cwd basename is slugified too",
			dir:     []string{"Plain Two"},
			want:    "plain-two",
			wantSrc: SourceDir,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			r := Resolver{
				Dir: filepath.Join(append([]string{base}, tt.dir...)...),
				Getenv: func(k string) string {
					if k == EnvVar {
						return tt.env
					}
					return ""
				},
			}
			got, err := r.Resolve(tt.override)
			if err != nil {
				t.Fatalf("Resolve(%q): %v", tt.override, err)
			}
			if got.Slug != tt.want {
				t.Errorf("slug = %q, want %q", got.Slug, tt.want)
			}
			if got.Source != tt.wantSrc {
				t.Errorf("source = %q, want %q", got.Source, tt.wantSrc)
			}
		})
	}
}

// TestResolveAtRoot covers the walk's termination condition and the
// empty-slug error together: at the root of the volume filepath.Dir is its own
// fixed point, and the basename slugifies away to nothing.
func TestResolveAtRoot(t *testing.T) {
	t.Parallel()
	r := Resolver{Dir: string(filepath.Separator), Getenv: func(string) string { return "" }}

	_, err := r.Resolve("")
	if err == nil {
		t.Fatal("Resolve at the filesystem root succeeded, want an error")
	}
	// The message has to name the fix, because there is no cwd the user can
	// move to that would help.
	if !strings.Contains(err.Error(), "--project") {
		t.Errorf("err = %q, want it to suggest --project", err)
	}
}

// TestResolveRejectsUnslugifiableOverride: an override made entirely of
// punctuation is a user error, not a project called "".
func TestResolveRejectsUnslugifiableOverride(t *testing.T) {
	t.Parallel()
	r := Resolver{Dir: t.TempDir(), Getenv: func(string) string { return "" }}

	if _, err := r.Resolve("///"); err == nil {
		t.Error("Resolve(\"///\") succeeded, want an error")
	}
}

func TestSlugify(t *testing.T) {
	t.Parallel()
	tests := []struct{ in, want string }{
		{"tt", "tt"},
		{"My Repo", "my-repo"},
		{"My-Repo", "my-repo"},
		{"my repo", "my-repo"},
		{"  padded  ", "padded"},
		{"a//b", "a-b"},
		{"a   b", "a-b"},
		{"go.mod_v2", "go.mod_v2"},
		{"--trimmed--", "trimmed"},
		{"café", "caf"},
		{"", ""},
		{"///", ""},
		{"/", ""},
	}
	for _, tt := range tests {
		if got := Slugify(tt.in); got != tt.want {
			t.Errorf("Slugify(%q) = %q, want %q", tt.in, got, tt.want)
		}
	}
}
