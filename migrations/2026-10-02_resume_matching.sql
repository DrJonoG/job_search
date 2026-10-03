-- Resume matching + optional Resume Matcher integration
-- Safe for existing installations.

CREATE TABLE IF NOT EXISTS `resume_match_scores` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `prompt_id` BIGINT NOT NULL DEFAULT 0,
  `resume_hash` VARCHAR(64) NOT NULL,
  `job_id` VARCHAR(255) NOT NULL,
  `match_score` DECIMAL(6,2) NOT NULL,
  `semantic_score` DECIMAL(6,2) NOT NULL,
  `title_score` DECIMAL(6,2) NOT NULL,
  `best_resume_chunk` TEXT NULL,
  `model_name` VARCHAR(255) NOT NULL,
  `analyzed_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_prompt_resume_job`
    (`prompt_id`, `resume_hash`, `job_id`),
  KEY `idx_job_id` (`job_id`),
  KEY `idx_match_score` (`match_score`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS `ai_prompt_resume_links` (
  `prompt_id` BIGINT NOT NULL,
  `source` VARCHAR(32) NOT NULL,
  `external_id` VARCHAR(128) NOT NULL,
  `external_name` VARCHAR(255) NOT NULL DEFAULT '',
  `synced_at` DATETIME NULL,
  `updated_at` TIMESTAMP NOT NULL
    DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`prompt_id`),
  KEY `idx_resume_source_external`
    (`source`, `external_id`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;
