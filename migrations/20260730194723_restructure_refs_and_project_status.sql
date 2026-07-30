-- Disable the enforcement of foreign-keys constraints
PRAGMA foreign_keys = off;
-- Create "new_comments" table
CREATE TABLE `new_comments` (
  `id` integer NOT NULL PRIMARY KEY AUTOINCREMENT,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `author` text NOT NULL,
  `body` text NOT NULL DEFAULT '',
  `issue_comments` integer NOT NULL,
  CONSTRAINT `comments_issues_comments` FOREIGN KEY (`issue_comments`) REFERENCES `issues` (`id`) ON UPDATE NO ACTION ON DELETE CASCADE
);
-- Copy rows from old table "comments" to new temporary table "new_comments".
-- `at` becomes created_at, and updated_at starts equal to it: an unedited
-- comment is one whose two stamps still match.
INSERT INTO `new_comments` (`id`, `created_at`, `updated_at`, `author`, `body`, `issue_comments`) SELECT `id`, `at`, `at`, `author`, `body`, `issue_comments` FROM `comments`;
-- Drop "comments" table after copying rows
DROP TABLE `comments`;
-- Rename temporary table "new_comments" to "comments"
ALTER TABLE `new_comments` RENAME TO `comments`;
-- Create "new_issues" table
CREATE TABLE `new_issues` (
  `id` integer NOT NULL PRIMARY KEY AUTOINCREMENT,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `title` text NOT NULL,
  `body` text NOT NULL DEFAULT '',
  `status` text NOT NULL DEFAULT 'todo',
  `priority` text NOT NULL DEFAULT 'normal',
  `closed_at` datetime NULL,
  `parent_id` integer NULL,
  `milestone_issues` integer NULL,
  `project_issues` integer NOT NULL,
  CONSTRAINT `issues_projects_issues` FOREIGN KEY (`project_issues`) REFERENCES `projects` (`id`) ON UPDATE NO ACTION ON DELETE NO ACTION,
  CONSTRAINT `issues_milestones_issues` FOREIGN KEY (`milestone_issues`) REFERENCES `milestones` (`id`) ON UPDATE NO ACTION ON DELETE SET NULL,
  CONSTRAINT `issues_issues_subtasks` FOREIGN KEY (`parent_id`) REFERENCES `issues` (`id`) ON UPDATE NO ACTION ON DELETE SET NULL
);
-- Copy rows from old table "issues" to new temporary table "new_issues"
INSERT INTO `new_issues` (`id`, `created_at`, `updated_at`, `title`, `body`, `status`, `priority`, `closed_at`, `milestone_issues`, `project_issues`) SELECT `id`, `created_at`, `updated_at`, `title`, `body`, `status`, `priority`, `closed_at`, `milestone_issues`, `project_issues` FROM `issues`;
-- Move decomposition out of "refs" and onto the issue itself, while refs still
-- has the `kind` column that distinguishes it. A subtask ref reads "parent is
-- blocked by child", so the parent is blocked_id and the child is blocker_id.
--
-- The old schema permitted a child several parents and the new one permits one,
-- so a child with more than one keeps its earliest ref and loses the rest.
UPDATE `new_issues` SET `parent_id` = (
  SELECT `blocked_id` FROM `refs`
  WHERE `refs`.`blocker_id` = `new_issues`.`id` AND `refs`.`kind` = 'subtask'
  ORDER BY `refs`.`id` LIMIT 1
);
-- Drop "issues" table after copying rows
DROP TABLE `issues`;
-- Rename temporary table "new_issues" to "issues"
ALTER TABLE `new_issues` RENAME TO `issues`;
-- Create "new_labels" table.
--
-- Rebuilt rather than altered in place: SQLite refuses to ADD COLUMN a NOT NULL
-- column with no default to a table that has rows.
CREATE TABLE `new_labels` (
  `id` integer NOT NULL PRIMARY KEY AUTOINCREMENT,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `name` text NOT NULL,
  `description` text NOT NULL DEFAULT ''
);
-- Copy rows from old table "labels" to new temporary table "new_labels"
INSERT INTO `new_labels` (`id`, `created_at`, `updated_at`, `name`, `description`) SELECT `id`, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, `name`, `description` FROM `labels`;
-- Drop "labels" table after copying rows
DROP TABLE `labels`;
-- Rename temporary table "new_labels" to "labels"
ALTER TABLE `new_labels` RENAME TO `labels`;
-- Create index "labels_name_key" to table: "labels"
CREATE UNIQUE INDEX `labels_name_key` ON `labels` (`name`);
-- Create "new_milestones" table
CREATE TABLE `new_milestones` (
  `id` integer NOT NULL PRIMARY KEY AUTOINCREMENT,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `name` text NOT NULL,
  `due` datetime NULL,
  `description` text NOT NULL DEFAULT '',
  `project_milestones` integer NOT NULL,
  CONSTRAINT `milestones_projects_milestones` FOREIGN KEY (`project_milestones`) REFERENCES `projects` (`id`) ON UPDATE NO ACTION ON DELETE NO ACTION
);
-- Copy rows from old table "milestones" to new temporary table "new_milestones"
INSERT INTO `new_milestones` (`id`, `created_at`, `updated_at`, `name`, `due`, `description`, `project_milestones`) SELECT `id`, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, `name`, `due`, `description`, `project_milestones` FROM `milestones`;
-- Drop "milestones" table after copying rows
DROP TABLE `milestones`;
-- Rename temporary table "new_milestones" to "milestones"
ALTER TABLE `new_milestones` RENAME TO `milestones`;
-- Create index "milestone_name_project_milestones" to table: "milestones"
CREATE UNIQUE INDEX `milestone_name_project_milestones` ON `milestones` (`name`, `project_milestones`);
-- Add column "status" to table: "projects"
ALTER TABLE `projects` ADD COLUMN `status` text NOT NULL DEFAULT 'active';
-- Create "new_refs" table
CREATE TABLE `new_refs` (
  `id` integer NOT NULL PRIMARY KEY AUTOINCREMENT,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `blocked_id` integer NOT NULL,
  `blocker_id` integer NOT NULL,
  CONSTRAINT `refs_issues_blocker` FOREIGN KEY (`blocker_id`) REFERENCES `issues` (`id`) ON UPDATE NO ACTION ON DELETE CASCADE,
  CONSTRAINT `refs_issues_blocked` FOREIGN KEY (`blocked_id`) REFERENCES `issues` (`id`) ON UPDATE NO ACTION ON DELETE CASCADE
);
-- Copy rows from old table "refs" to new temporary table "new_refs".
-- Dependencies only: the subtask rows became issues.parent_id above, and
-- carrying them over would restate every parent link as a blocking one.
INSERT INTO `new_refs` (`id`, `created_at`, `updated_at`, `blocked_id`, `blocker_id`) SELECT `id`, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, `blocked_id`, `blocker_id` FROM `refs` WHERE `kind` = 'dep';
-- Drop "refs" table after copying rows
DROP TABLE `refs`;
-- Rename temporary table "new_refs" to "refs"
ALTER TABLE `new_refs` RENAME TO `refs`;
-- Create index "ref_blocked_id_blocker_id" to table: "refs"
CREATE UNIQUE INDEX `ref_blocked_id_blocker_id` ON `refs` (`blocked_id`, `blocker_id`);
-- Enable back the enforcement of foreign-keys constraints
PRAGMA foreign_keys = on;
