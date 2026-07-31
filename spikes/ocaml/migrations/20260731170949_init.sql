-- Create "projects" table
CREATE TABLE `projects` (
  `id` integer NULL PRIMARY KEY AUTOINCREMENT,
  `slug` text NOT NULL,
  `title` text NOT NULL,
  `body` text NOT NULL,
  `status` text NOT NULL,
  `created_at` text NOT NULL,
  `updated_at` text NOT NULL,
  `deleted_at` text NULL,
  CHECK (status IN ('active', 'archived'))
) STRICT;
-- Create index "projects_slug_live" to table: "projects"
CREATE UNIQUE INDEX `projects_slug_live` ON `projects` (`slug`) WHERE deleted_at IS NULL;
-- Create "issues" table
CREATE TABLE `issues` (
  `id` integer NULL PRIMARY KEY AUTOINCREMENT,
  `project_id` integer NOT NULL,
  `title` text NOT NULL,
  `body` text NOT NULL,
  `status` text NOT NULL,
  `priority` integer NOT NULL,
  `status_note` text NULL,
  `created_at` text NOT NULL,
  `updated_at` text NOT NULL,
  `deleted_at` text NULL,
  CONSTRAINT `0` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON UPDATE NO ACTION ON DELETE CASCADE,
  CHECK (status IN ('todo', 'doing', 'done')),
  CHECK (priority IN (0, 1))
) STRICT;
-- Create index "issues_by_project" to table: "issues"
CREATE INDEX `issues_by_project` ON `issues` (`project_id`, `status`) WHERE deleted_at IS NULL;
