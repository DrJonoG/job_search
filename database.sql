-- ============================================================
-- job_search – MySQL Database Schema
-- ============================================================
-- Usage:
--   1. Open phpMyAdmin (http://localhost/phpmyadmin)
--   2. Create a new database called `job_search`
--   3. Select the database, go to the SQL tab
--   4. Paste this entire file and click "Go"
--
-- Or from the MySQL CLI:
--   mysql -u root -p < database.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS `job_search`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `job_search`;

-- ── Sources reference table ──────────────────────────────────

CREATE TABLE IF NOT EXISTS `sources` (
  `id`               INT UNSIGNED    NOT NULL AUTO_INCREMENT,
  `name`             VARCHAR(100)    NOT NULL,
  `website`          VARCHAR(512)    NOT NULL DEFAULT '',
  `requires_api_key` TINYINT(1)      NOT NULL DEFAULT 0,
  `is_free`          TINYINT(1)      NOT NULL DEFAULT 1,
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_source_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed default sources
INSERT IGNORE INTO `sources` (`name`, `website`, `requires_api_key`, `is_free`) VALUES
  ('RemoteOK',           'https://remoteok.com/',                    0, 1),
  ('Arbeitnow',          'https://arbeitnow.com/',                  0, 1),
  ('The Muse',           'https://www.themuse.com/',                 0, 1),
  ('Jobicy',             'https://jobicy.com/',                     0, 1),
  ('Remotive',           'https://remotive.com/',                    0, 1),
  ('WeWorkRemotely',     'https://weworkremotely.com/',              0, 1),
  ('JobSpy',             'https://github.com/Bunsly/JobSpy',         0, 1),
  ('LinkedIn',           'https://www.linkedin.com/jobs/',          0, 1),
  ('WorkingNomads',      'https://www.workingnomads.com/',          0, 1),
  ('Lobsters',           'https://lobste.rs/t/job',                 0, 1),
  ('Greenhouse',         'https://boards.greenhouse.io/',           0, 1),
  ('HN Who is hiring',   'https://news.ycombinator.com/',           0, 1),
  ('Totaljobs',          'https://www.totaljobs.com/',              0, 1),
  ('Remote.co',          'https://remote.co/',                       0, 1),
  ('GOV.UK Find a Job',  'https://findajob.dwp.gov.uk/',            0, 1),
  ('Adzuna',             'https://developer.adzuna.com/',           1, 1),
  ('Reed',               'https://www.reed.co.uk/developers/',      1, 1),
  ('USAJobs',            'https://developer.usajobs.gov/',          1, 1),
  ('Jooble',             'https://jooble.org/api/about',             1, 1),
  ('Google Jobs',        'https://serpapi.com/',                    1, 1),
  ('Findwork',           'https://findwork.dev/developers/',         1, 1),
  ('CareerJet',          'https://www.careerjet.com/partners/api',   1, 1),
  ('JobData',            'https://jobdataapi.com/docs/',             0, 1),
  ('LinkedIn (Direct)',  'https://www.linkedin.com/jobs/',           0, 1),
  ('Lever',              'https://jobs.lever.co/',                   0, 1),
  ('Ashby',              'https://jobs.ashbyhq.com/',                0, 1),
  ('Workable',           'https://apply.workable.com/',              0, 1),
  ('JobsCollider',       'https://jobscollider.com/',                0, 1),
  ('DevITjobs',          'https://devitjobs.uk/',                    0, 1);

-- ── Jobs table ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `jobs` (
  `id`               INT UNSIGNED    NOT NULL AUTO_INCREMENT,
  `job_id`           VARCHAR(64)     NOT NULL COMMENT 'MD5 hash used for deduplication',
  `title`            VARCHAR(512)    NOT NULL DEFAULT '',
  `company`          VARCHAR(255)    NOT NULL DEFAULT '',
  `location`         VARCHAR(255)    NOT NULL DEFAULT '',
  `description`      MEDIUMTEXT      NOT NULL COMMENT 'Full job description text',
  `url`              VARCHAR(2048)   NOT NULL DEFAULT '',
  `source`           VARCHAR(100)    NOT NULL DEFAULT '' COMMENT 'Source name (denormalised for FULLTEXT / query perf)',
  `remote`           VARCHAR(50)     NOT NULL DEFAULT 'Unknown' COMMENT 'Remote / On-site / Hybrid / Unknown',
  `salary_min`       DECIMAL(12,2)   DEFAULT NULL,
  `salary_max`       DECIMAL(12,2)   DEFAULT NULL,
  `salary_currency`  VARCHAR(10)     NOT NULL DEFAULT '',
  `job_type`         VARCHAR(50)     NOT NULL DEFAULT '' COMMENT 'Full-time, Part-time, Contract, etc.',
  `experience_level` VARCHAR(50)     NOT NULL DEFAULT '',
  `date_posted`      VARCHAR(50)     NOT NULL DEFAULT '' COMMENT 'ISO date when available; used for default sort (newest first)',
  `date_scraped`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `tags`             TEXT            NOT NULL COMMENT 'Comma-separated tags',
  `company_logo`     VARCHAR(2048)   NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_job_id` (`job_id`),
  INDEX `idx_source`       (`source`),
  INDEX `idx_remote`       (`remote`),
  INDEX `idx_job_type`     (`job_type`),
  INDEX `idx_date_scraped` (`date_scraped`),
  INDEX `idx_date_posted`  (`date_posted`),
  FULLTEXT INDEX `ft_search` (`title`, `company`, `description`, `tags`, `location`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Favourites table ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `favourites` (
  `id`          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `job_id`      VARCHAR(64)   NOT NULL,
  `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_fav_job` (`job_id`),
  CONSTRAINT `fk_fav_job` FOREIGN KEY (`job_id`)
    REFERENCES `jobs` (`job_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Applications table ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS `applications` (
  `id`          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `job_id`      VARCHAR(64)   NOT NULL,
  `applied_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `notes`       TEXT          NOT NULL DEFAULT (''),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_app_job` (`job_id`),
  CONSTRAINT `fk_app_job` FOREIGN KEY (`job_id`)
    REFERENCES `jobs` (`job_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Not Interested table ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS `not_interested` (
  `id`          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `job_id`      VARCHAR(64)   NOT NULL,
  `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ni_job` (`job_id`),
  CONSTRAINT `fk_ni_job` FOREIGN KEY (`job_id`)
    REFERENCES `jobs` (`job_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Saved Searches table ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS `saved_searches` (
  `id`          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `name`        VARCHAR(255)  NOT NULL DEFAULT '',
  `params`      JSON          NOT NULL COMMENT 'All search parameters as JSON',
  `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Saved Board Searches table ────────────────────────────────

CREATE TABLE IF NOT EXISTS `saved_board_searches` (
  `id`          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `name`        VARCHAR(255)  NOT NULL DEFAULT '',
  `params`      JSON          NOT NULL COMMENT 'All board filter parameters as JSON',
  `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Notes table ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `notes` (
  `id`          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `title`       VARCHAR(255)  NOT NULL DEFAULT '',
  `body`        MEDIUMTEXT    NOT NULL COMMENT 'Rich text content (HTML)',
  `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  FULLTEXT INDEX `ft_notes_search` (`title`, `body`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
