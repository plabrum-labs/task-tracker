-- Create "comments" table
CREATE TABLE `comments` (`id` integer NOT NULL PRIMARY KEY AUTOINCREMENT, `at` datetime NOT NULL, `author` text NOT NULL, `body` text NOT NULL DEFAULT (''), `issue_comments` integer NOT NULL, CONSTRAINT `comments_issues_comments` FOREIGN KEY (`issue_comments`) REFERENCES `issues` (`id`) ON DELETE CASCADE);
-- Create "issues" table
CREATE TABLE `issues` (`id` integer NOT NULL PRIMARY KEY AUTOINCREMENT, `created_at` datetime NOT NULL, `updated_at` datetime NOT NULL, `title` text NOT NULL, `body` text NOT NULL DEFAULT (''), `status` text NOT NULL DEFAULT ('todo'), `priority` text NOT NULL DEFAULT ('normal'), `closed_at` datetime NULL, `milestone_issues` integer NULL, `project_issues` integer NOT NULL, CONSTRAINT `issues_milestones_issues` FOREIGN KEY (`milestone_issues`) REFERENCES `milestones` (`id`) ON DELETE SET NULL, CONSTRAINT `issues_projects_issues` FOREIGN KEY (`project_issues`) REFERENCES `projects` (`id`) ON DELETE NO ACTION);
-- Create "labels" table
CREATE TABLE `labels` (`id` integer NOT NULL PRIMARY KEY AUTOINCREMENT, `name` text NOT NULL, `description` text NOT NULL DEFAULT (''));
-- Create index "labels_name_key" to table: "labels"
CREATE UNIQUE INDEX `labels_name_key` ON `labels` (`name`);
-- Create "milestones" table
CREATE TABLE `milestones` (`id` integer NOT NULL PRIMARY KEY AUTOINCREMENT, `name` text NOT NULL, `due` datetime NULL, `description` text NOT NULL DEFAULT (''), `project_milestones` integer NOT NULL, CONSTRAINT `milestones_projects_milestones` FOREIGN KEY (`project_milestones`) REFERENCES `projects` (`id`) ON DELETE NO ACTION);
-- Create index "milestone_name_project_milestones" to table: "milestones"
CREATE UNIQUE INDEX `milestone_name_project_milestones` ON `milestones` (`name`, `project_milestones`);
-- Create "projects" table
CREATE TABLE `projects` (`id` integer NOT NULL PRIMARY KEY AUTOINCREMENT, `created_at` datetime NOT NULL, `updated_at` datetime NOT NULL, `slug` text NOT NULL, `title` text NOT NULL DEFAULT (''), `description` text NOT NULL DEFAULT (''));
-- Create index "projects_slug_key" to table: "projects"
CREATE UNIQUE INDEX `projects_slug_key` ON `projects` (`slug`);
-- Create "refs" table
CREATE TABLE `refs` (`id` integer NOT NULL PRIMARY KEY AUTOINCREMENT, `kind` text NOT NULL DEFAULT ('dep'), `blocked_id` integer NOT NULL, `blocker_id` integer NOT NULL, CONSTRAINT `refs_issues_blocked` FOREIGN KEY (`blocked_id`) REFERENCES `issues` (`id`) ON DELETE CASCADE, CONSTRAINT `refs_issues_blocker` FOREIGN KEY (`blocker_id`) REFERENCES `issues` (`id`) ON DELETE CASCADE);
-- Create index "ref_blocked_id_blocker_id" to table: "refs"
CREATE UNIQUE INDEX `ref_blocked_id_blocker_id` ON `refs` (`blocked_id`, `blocker_id`);
-- Create "issue_labels" table
CREATE TABLE `issue_labels` (`issue_id` integer NOT NULL, `label_id` integer NOT NULL, PRIMARY KEY (`issue_id`, `label_id`), CONSTRAINT `issue_labels_issue_id` FOREIGN KEY (`issue_id`) REFERENCES `issues` (`id`) ON DELETE CASCADE, CONSTRAINT `issue_labels_label_id` FOREIGN KEY (`label_id`) REFERENCES `labels` (`id`) ON DELETE CASCADE);
