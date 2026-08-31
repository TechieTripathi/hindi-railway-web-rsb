# Product Requirements Document (PRD)

## 1. Product Overview

AI News Agency is a Flask-based content operations dashboard for news editors. It helps non-technical users collect  news from official sources and the web, generate AI-assisted bilingual stories, and review quality scores before publication.

## 2. Problem Statement

News editors need a fast way to:
- discover relevant news from official and public sources,
- reduce manual copywriting work,
- generate bilingual stories and social media variants,
- evaluate article quality using built-in scoring signals,
- archive or delete irrelevant content from the review queue.

## 3. Product Goals

- Reduce manual effort in gathering and drafting news stories.
- Provide a simple dashboard for reviewing and publishing-ready content.
- Support bilingual output for Hindi and English.
- Improve trust in AI-generated content through visibility of source validation and quality scores.

## 4. Target Users

- newsroom editors
- Content operations teams
- Social media/content coordinators
- Internal stakeholders reviewing AI-generated news drafts

## 5. Core User Stories

- As an editor, I want to crawl official press releases so I can review the latest updates.
- As an editor, I want to search the web for relevant news so I can capture broader coverage.
- As an editor, I want to generate bilingual news stories automatically so I can publish faster.
- As an editor, I want to view fact score, plagiarism score, and source confidence so I can judge quality.
- As an editor, I want to archive or delete irrelevant items so the review queue stays focused.

## 6. Scope

### In Scope
- Official crawling
- Web news discovery and source resolution
- AI story generation with translation
- Social media post generation
- Quality scoring and confidence badges
- Archive/delete workflow
- Dashboard filtering and article detail views

### Out of Scope
- Full publishing workflow to CMS or social platforms
- User authentication and permissions
- Advanced editorial approvals workflow
- Production-grade infrastructure and scaling

## 7. Functional Requirements

### 7.1 Dashboard and Navigation
- The system shall provide two dashboards: RSB Search and Web News Feed.
- Each dashboard shall display article cards with title, source, date, topics, and status.
- Users shall be able to filter by zone, topic, source, and article status.

### 7.2 Data Collection
- The system shall crawl official portals for recent press releases.
- The system shall support configurable zone selection and date range filters.
- The system shall collect web news via Google News RSS and resolve article URLs.
- The system shall store crawl results as JSON output files.

### 7.3 AI Generation
- The system shall generate bilingual stories from source content.
- The system shall generate social media variants in both languages.
- The system shall assign impact level, topics, and themes.
- The system shall produce fact score and plagiarism score.

### 7.4 Source Validation
- The system shall display source confidence badges based on the number of distinct sources.
- The system shall merge related articles into a unified view where applicable.

### 7.5 Review and Management
- The system shall allow users to mark articles as new, archived, or deleted.
- The system shall persist article status across sessions.
- The system shall expose logs for crawl and search operations for debugging.

## 8. Non-Functional Requirements

- The system shall be easy to run locally with Python and Flask.
- The UI shall be lightweight and usable by non-technical users.
- The system shall tolerate missing or partial source data without crashing.
- The system shall be understandable enough for an internal prototype or POC.

## 9. Success Metrics

- Time saved per article in drafting and summarization.
- Reduction in manual research steps for editors.
- Percentage of generated stories reviewed without correction.
- User satisfaction with bilingual output and confidence scoring.
