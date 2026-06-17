---
name: paper-mode
description: >-
  Research paper development workflow for academic projects. Orchestrates
  the iterative loop of paper writing, concept figure design, experiment
  planning, code development, data collection, and chart generation.
  Use when entering "Develop for Paper Mode" or when the project goal
  is producing an academic publication (conference/journal paper).
  Triggers on "paper mode", "develop for paper", "write paper",
  or any request to set up a research paper development workflow.
user-invocable: true
---

# Develop for Paper Mode

A structured workflow for research paper development that tightly
couples paper writing, experiment design, code development, and data
visualization. Everything serves the paper -- code is written to run
experiments, experiments produce data for charts, charts support
claims in the paper.

## Core Principle

> Code exists to run experiments. Experiments exist to produce data.
> Data exists to make charts. Charts exist to support the paper.
> **Everything serves the paper.**

We are NOT building a product. We are building prototypes to validate
research hypotheses and collect evidence for publication.

## Directory Convention

```
PROJ_ROOT/              # Project root (codebase)
PROJ_ROOT/paper/        # PAPER_ROOT: LaTeX paper (may be a git submodule)
  sections/             # sec-00-abstract.tex, sec-10-intro.tex, ...
  figures/              # Concept figures: .txt (ASCII source) + .pdf
  charts/
    concept/            # Concept charts: .txt (ASCII mockup) + .pdf
    plot_*.py           # Real data-driven chart scripts (Phase 3)
    *.pdf               # Generated real charts (Phase 3)
  data/                 # Cleaned experiment data (CSV)
  references.bib        # Bibliography
  main.tex              # Main LaTeX document
  Makefile              # Build pipeline
PROJ_ROOT/temp/         # TEMP_ROOT: Temporary working area (gitignored)
  data/                 # Raw experiment output (JSON, logs)
  paper-experiments.md  # Experiment specification ("contract")
```

## Four-Phase Workflow

### Phase 0: Literature Survey and Thesis Formation

**Goal:** Establish what the paper is about, what is novel, what
exists already.

**Process:**
1. Broad literature exploration using extended AI tools (Opus Extended,
   GPT Pro, Gemini Deep Research) and manual reading.
2. Produce survey reports in `PAPER_ROOT/reports/`.
3. Collect references into `references.bib` during the survey.
4. Through intensive brainstorming with AI, crystallize:
   - Core thesis and contributions
   - Paper outline (section structure)
   - Key claims that need experimental support

**Output:** `PAPER_ROOT/reports/*.md`, `references.bib`, mental model
of the paper's argument.

### Phase 1: Paper Skeleton and Concept Figures

**Goal:** Produce a compilable paper draft with structure, arguments,
and placeholder figures.

**Process:**
1. HIGH-INTERACTION brainstorming with AI, section by section.
   Do NOT auto-generate large blocks without discussion.
2. Write LaTeX sections in `PAPER_ROOT/sections/sec-XX-name.tex`.
   - Number sections as 00, 10, 20, ... (gaps allow future insertion
     at 05, 15, 25, ...).
   - Separate design/spec (what and why) from implementation (how).
     Design goes in contribution sections; implementation goes in
     methodology.
3. Create ASCII art mockups for concept figures in
   `PAPER_ROOT/figures/*.txt`.
   - Convert to PDF via `PAPER_ROOT/figures/ascii2pdf.sh`.
   - Insert into tex with `\includegraphics`.
   - When the user later draws proper figures, just replace the .pdf.
4. The paper must always compile (`make` in `PAPER_ROOT/`).
   Figures/charts not yet created should be commented out in tex.

**Output:** Compilable PDF with text + ASCII placeholder figures.

### Phase 2: Experiment Design (The Contract)

**Goal:** Define exactly what experiments to run, what data to collect,
and what charts to produce -- before writing any code.

**Process:**
1. For each claim in the paper that needs data support, work backwards:
   - What chart would convincingly demonstrate this claim?
   - What data does that chart need?
   - What experiment produces that data?
   - What code/infrastructure is needed to run that experiment?
2. Write the experiment specification into
   `TEMP_ROOT/paper-experiments.md`.
   This file is the "contract" between the paper and the codebase:
   - Paper side: what data is needed, what figures will be drawn.
   - Code side: what to build, what to run, what to collect.
3. Verify that the codebase can support each experiment. If not,
   identify what code changes are needed.

**Output:** `TEMP_ROOT/paper-experiments.md` with experiment
specifications, data requirements, and code gap analysis.

### Phase 3: Build, Run, Collect, Plot

**Goal:** Implement code, run experiments, collect data, produce charts,
and insert them into the paper.

**Process:**
1. **Code development** follows the experiment contract. Use
   superman flow (superpowers + humanize) for large implementations.
   Remember: code is for evaluation, not for production.
2. **Run experiments** -> raw data to `TEMP_ROOT/data/`.
3. **Clean data** -> CSV to `PAPER_ROOT/data/`.
4. **Plot charts** via scripts in `PAPER_ROOT/charts/plot_*.py`
   -> PDF to `PAPER_ROOT/charts/`.
5. **Insert** chart into tex via `\includegraphics`.
6. **Verify** the chart supports the paper's claim. If not, iterate.

**Output:** Paper with real data-driven charts.

## Concept Charts (ASCII Mockups for Data-Driven Figures)

Before real experiment data exists, create ASCII mockup charts that
show the **intended visualization** for each data-driven figure. These
serve three purposes:

1. **Visual layout**: Inserted into the compiled PDF so the paper's
   visual flow can be assessed before any experiments run.
2. **Experiment contract anchor**: Each concept chart in
   `paper/charts/concept/` is referenced from the experiment spec
   (`temp/paper-experiments.md`), making explicit: "experiment E_x
   produces data for chart C_y, whose intended appearance is Z."
3. **No fake data risk**: ASCII mockups are clearly placeholders --
   they cannot be confused with real results or accidentally left in
   the final submission.

### Workflow

1. During paper writing (Phase 1 or iterative W3), create ASCII
   mockup in `PAPER_ROOT/charts/concept/<chart_name>.txt`.
   Use box-drawing characters, ASCII bar charts, scatter plots, etc.
   Include axis labels, legend, and annotation of expected trends.
2. Convert to PDF using the same `ascii2pdf.sh` pipeline as concept
   figures (or a dedicated `charts/concept/ascii2pdf.sh`).
3. Insert into tex with `\includegraphics{charts/concept/<name>.pdf}`.
4. In `temp/paper-experiments.md`, each experiment entry should
   reference its concept chart:
   ```
   **Concept chart:** charts/concept/<chart_name>.txt
   **Real chart (when ready):** charts/<chart_name>.pdf
   ```
5. When real data is collected and a `plot_*.py` script generates the
   real chart PDF, update the tex `\includegraphics` path from
   `charts/concept/<name>.pdf` to `charts/<name>.pdf`.

### ASCII Chart Style Guide

```
Throughput (ops/cycle)
  |
  |  ###
  |  ###  ===
  |  ###  ===  +++
  |  ###  ===  +++
  +--+---------+---------> Domain
     LLM  SLAM  ZK

  ### = Bilevel   === = Heuristic   +++ = Round-Robin
```

Use different fill characters (###, ===, +++, ...) for different
series. Include axis titles, legend, and brief annotation of the
expected trend (e.g., "Bilevel consistently outperforms baselines").

## Three Interleaved Workflows (During Iteration)

Once the skeleton exists, development proceeds as three interleaved
workflows triggered by paper discussion:

### W1: Paper Text -> modify tex
- Discuss section by section with user.
- If a concept figure is needed, go to W2.
- If data-driven evidence is needed, go to W3.

### W2: Concept Figures
- Discuss what to draw with user.
- User may provide a sketch image; digest and write ASCII mockup
  into `PAPER_ROOT/figures/*.txt`.
- Run `ascii2pdf.sh` to generate placeholder PDF.
- User draws the final figure later, replacing the PDF.
- Update tex to include the figure.

### W3: Data Charts (Experiment-Driven)
- Discuss what experiment to run; inspect codebase to verify support.
- **First**: Create an ASCII concept chart in
  `PAPER_ROOT/charts/concept/<chart_name>.txt` showing the intended
  visualization. Convert to PDF and insert into tex immediately.
  This lets the paper's visual layout be assessed before data exists.
- Update experiment spec in `TEMP_ROOT/paper-experiments.md` with a
  reference to the concept chart.
- If code changes are needed, fix code first.
- Execute experiment -> raw data to `TEMP_ROOT/data/`.
- Clean data -> CSV to `PAPER_ROOT/data/`.
- Write plotting script in `PAPER_ROOT/charts/plot_<name>.py`
  -> real PDF to `PAPER_ROOT/charts/`.
- Update tex `\includegraphics` path from `charts/concept/` to
  `charts/` to swap in the real chart.

## Architecture Paper Structure (11--12 Pages, 3 Contributions)

This is the canonical structure for a computer architecture research
paper. All numbers are approximate targets for a double-column format
(e.g., ACM sigconf, IEEE micro). Total budget: 11--12 pages, 3 major
contributions.

### Layer 1: Front Matter (~3 pages, 2 figures, 1 table)

| Component | Budget | Assets |
|-----------|--------|--------|
| Abstract | ~0.5 column | -- |
| Introduction | 3--4 columns | 1 overview figure (problem + solution) |
| Background | 1--2 columns | 1 concept figure + 1 comparison table |

- **Abstract**: Problem, solution (3 contributions), results summary.
- **Introduction**: Architecture benefit -> scaling limits ->
  heterogeneity motivation -> research gaps -> coupled problem ->
  key insight -> 3 contributions -> results preview -> paper org.
- **Background**: Primer on domain concepts, data model figure,
  comparison table against related work (5--7 baselines, 4--6
  capability dimensions).

### Layer 2: Main Body (~6 pages = 3 contributions x 2 pages)

Each contribution section follows the **a + (b+c) x N** pattern:

#### Part a -- Problem & Motivation (first subsection)

The reader just finished the intro and background. This subsection
recaps and deepens the specific problem this contribution addresses:

1. **Expand the problem**: What exactly is being solved? Why is it
   hard? Briefly cite what prior work does and where it falls short.
2. **Identify N key challenges** (typically N = 2--3): Each challenge
   becomes the seed for a design subsection in Part b.
3. **Transition to insight**: End with a natural pivot -- "given these
   challenges, we observe that..." -- so the reader feels: "OK, there
   is a real problem, the authors have a concrete idea, let me see
   how they solve it."

The purpose of Part a is to make the reader care about the problem
before seeing the solution.

#### Part b -- Design & Architecture (subsections 2 through N+1)

Each subsection addresses one challenge from Part a:

1. **Anchor to the challenge**: "To address Challenge X from above..."
2. **Design motivation**: Why this approach? What alternatives were
   considered and why were they rejected?
3. **Architecture details**: Formal definitions, algorithms,
   component interactions, data contracts.
4. **Figure references**: Each subsection should reference at least
   one figure that illustrates the design.

#### Part c -- Forward Reference (end of each Part b subsection)

A brief paragraph (2--3 sentences) at the end of each Part b that:
- States this design is implemented using [brief method hint]
  (see Methodology, Section X).
- States the effectiveness is evaluated in [brief experiment hint]
  (see Evaluation, Section Y).

This creates bidirectional traceability: readers of the design know
where to find the evaluation; readers of the evaluation know which
design is being validated.

#### Figure Budget per Contribution

Each contribution gets ~2 pages and needs 2--3 concept figures:
- Option A: 2 half-column (1/4 page) figures
- Option B: 1 half-column figure + 2 quarter-column (1/8 page) figures

Total concept figures for main body: 6--9.
Combined with front matter: **8--11 concept figures** total.

### Layer 3: Methodology (~1 page, 2--3 tables)

How the designs from the main body are realized as code:

| Table | Content |
|-------|---------|
| Benchmark table | Applications, kernels, dominant compute |
| Configuration table | Hardware parameters, design space |
| (Optional) Baseline table | Comparison strategies |

Key rule: methodology describes the HOW (tools, parameters, timeouts,
heuristic tuning). The main body describes the WHAT and WHY.

### Layer 4: Evaluation (~2--3 pages, 6--9 data-driven charts)

Each evaluation subsection corresponds to one or more Part b
subsections from the main body. Structure:

1. **Anchor to design**: "This experiment evaluates the [design name]
   from Section X, addressing [challenge]."
2. **Setup**: What is varied, what is held constant.
3. **Data chart**: The primary evidence.
4. **Analysis**: What the data shows, why it matters.

The number of evaluation subsections equals the total N across all
contributions. Some may need 2 charts.

**Back-reference rule**: Every eval subsection must cite the specific
design subsection it validates. Every Part c in the main body must
cite the specific eval subsection. This creates a closed loop.

Total data-driven charts: **6--9** (not concept figures -- these are
generated from experiment data).

### Layer 5: Related Work + Conclusion (~0.5--0.75 pages)

- **Related work**: Organized by research thread, each paragraph
  contrasts prior work with the current paper's approach.
- **Conclusion**: Restate contributions with quantitative results,
  2--3 architectural insights, limitations, future work.

### Asset Summary

| Asset Type | Count | Location |
|------------|-------|----------|
| Concept figures | 8--11 | `figures/` |
| Data-driven charts | 6--9 | `charts/` |
| Tables | 3--5 | inline in tex |
| Algorithm pseudocode | 1--2 | inline in tex |

### Traceability Matrix

The paper should satisfy this traceability:

```
Intro (contribution list)
  |
  v
Main Body: Part a (problem) -> Part b (design) -> Part c (fwd ref)
  |                              |                     |
  |                              v                     v
  |                         figures/             Methodology (impl)
  |                                                    |
  v                                                    v
Evaluation: back-ref to Part b, uses impl from Methodology
  |
  v
Conclusion: restates contributions with eval numbers
```

Every claim in the paper must trace to either:
- A concept figure (design motivation)
- A data chart (experimental evidence)
- A formal argument (proof or analysis)

## Key Rules

1. **Paper always compiles.** Broken `\includegraphics` refs are
   commented out, never left dangling.
2. **HIGH-INTERACTION mode.** Every section, claim, and figure needs
   brainstorming with the user. No large auto-generated text blocks.
3. **Section numbering uses gaps.** 00, 10, 20, ... so 05/15/25 can
   be inserted later.
4. **Design vs implementation separation.** Contribution sections
   describe the what and why (algorithms, architecture, formulation).
   Methodology section describes the how (tools, parameters, setup).
5. **Everything serves the paper.** Do not build features that do
   not contribute to a figure, table, or claim in the paper.
6. **Experiment contract first.** Before writing code for experiments,
   the contract (`paper-experiments.md`) must specify what data is
   needed and why.
7. **a + (b+c) x N pattern.** Every main body contribution section
   must have a motivation subsection (Part a) before diving into
   design (Part b), with forward references (Part c) to eval.
8. **Bidirectional traceability.** Design sections forward-reference
   eval; eval sections back-reference design. Readers can navigate
   in either direction.
9. **Brainstorm before restructuring contribution sections.**
   Restructuring a main body contribution section (sec-30/40/50)
   requires interactive brainstorming with the user BEFORE any
   implementation. The brainstorming must cover: code audit findings,
   challenge identification (what is N?), figure allocation, eval
   mapping, and concept chart design. After user approval, proceed
   directly to implementation (no separate spec/plan document needed
   since the paper itself IS the document). This is a paper-mode
   specific adaptation: skip the plan/execution phases of standard
   superpowers workflows.
10. **Concept charts must be visible in the PDF.** Concept charts
    in `charts/concept/` must be inserted into the evaluation
    section via `\includegraphics` (not just referenced in TODO
    text). Use a figure environment with a red TODO note in the
    caption indicating the chart needs real data. This ensures the
    paper's visual layout can be assessed at all times. When real
    charts are ready, update the `\includegraphics` path from
    `charts/concept/<name>.pdf` to `charts/<name>.pdf`.
11. **Numbered asset filenames with gaps.** Figures and concept
    charts use numbered prefixes aligned with their section, with
    gaps for future insertion:
    - `figures/fig-NN-name.{txt,pdf}` where NN aligns with the
      section number (e.g., fig-30 for sec-30, fig-32/34 for
      subsections within sec-30). Use increments of 2 within a
      section to leave room for insertions.
    - `charts/concept/chart-NN-name.{txt,pdf}` where NN aligns
      with the evaluation section number (e.g., chart-70 for the
      first eval chart in sec-70, chart-72/74 for subsequent).
    This makes the visual order immediately apparent in file
    listings and leaves room for inserting new assets between
    existing ones.
