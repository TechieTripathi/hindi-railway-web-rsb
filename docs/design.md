# Design Flow and Requirement Specifications

## 1. System Overview

The application is organized around a simple content pipeline:
1. Collect article candidates from official sources or web news.
2. Normalize and deduplicate content.
3. Generate AI-assisted editorial outputs.
4. Display results in dashboards for review and action.

## 2. High-Level Design Flow

### A. Input Collection Layer
- Crawl official portals example railways using the crawler module.
- Search the web using Google News RSS and Brave Search integration.
- Store raw results in JSON files for downstream processing.

### B. Processing Layer
- Deduplicate entries using hashing logic.
- Normalize article metadata such as title, source, date, and body.
- Generate AI outputs using the engine module.
- Compute quality metrics: fact score, plagiarism score, impact, topics, and source confidence.

### C. Presentation Layer
- Render two dashboards using Flask templates.
- Show article cards and detail views.
- Enable filtering, status updates, and log viewing.

## 3. Component Design

### 3.1 Flask Application Layer
- Handles routes for dashboards, crawl triggers, generation triggers, settings, and status.
- Coordinates subprocess execution for crawl and AI generation tasks.

### 3.2 Crawler Module
- Pulls data from zone-specific portals.
- Supports zone selection and date range filters.
- Writes crawl results to the crawl output file.

### 3.3 Web Agent Module
- Searches Google News RSS.
- Resolves article URLs via Brave Search and page extraction.
- Extracts content body and resolves source information.
- Writes web crawl results to the web crawl output file.

### 3.4 AI Engine Module
- Sends article content to Anthropic Claude.
- Produces bilingual stories, social posts, impact analysis, topics, themes, and quality scores.
- Persists generated outputs to agency output files.

### 3.5 Storage Layer
- Uses JSON files for persistence in the prototype state.
- Keeps separate files for crawl results, agency results, article statuses, and web settings.

## 4. Requirement Specifications

### Functional Specs

#### FR-01: Crawl official railway content
- The system shall allow users to initiate a crawl for selected railway zones.
- The system shall support optional date filters.
- The system shall store crawl output in a machine-readable format.

#### FR-02: Web news discovery
- The system shall allow users to configure keywords and search limits.
- The system shall fetch relevant articles from Google News RSS.
- The system shall attempt to resolve article URLs to real source pages.

#### FR-03: Article deduplication
- The system shall prevent duplicate articles from being displayed multiple times.
- The system shall use a consistent hashing approach for duplicate detection.

#### FR-04: AI-assisted article generation
- The system shall generate a paraphrased story in the source language.
- The system shall generate a translated version in the other language.
- The system shall create social media variants for each language.

#### FR-05: Quality scoring
- The system shall compute a fact score between 0 and 100.
- The system shall compute a plagiarism score between 0 and 100.
- The system shall provide impact level and reasoning.

#### FR-06: Review workflow
- The system shall allow users to mark an article as archived or deleted.
- The system shall preserve these statuses across sessions.

#### FR-07: Dashboard experience
- The system shall display all articles in a review-friendly dashboard.
- The system shall allow filtering by zone, topic, source, and status.

### Non-Functional Specs

#### NFR-01: Usability
- The interface shall be simple enough for non-technical editors to use.

#### NFR-02: Reliability
- The app shall continue to function gracefully even when some article bodies are missing.

#### NFR-03: Extensibility
- The system architecture shall allow future integration with a database or CMS.

#### NFR-04: Maintainability
- Core responsibilities shall remain separated across crawler, web agent, engine, and Flask routes.

## 5. User Journey Flow

### Web News Flow
1. User opens the web news dashboard.
2. User enters search keywords and limits.
3. User triggers web search.
4. System discovers, resolves, and extracts articles.
5. User triggers AI generation.
6. User reviews multilingual content and scores.
7. User archives or deletes low-value content.

## 6. Suggested Implementation Phases

### Phase 1 — MVP
- Basic crawl and dashboard review
- AI generation for article content
- Status management and filtering

### Phase 2 — Quality and Validation
- Better scoring explanation and confidence badges
- Better deduplication and grouping

### Phase 3 — Workflow Expansion
- CMS integration
- Authentication
- Database-backed storage
- Approval workflow
