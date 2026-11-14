# AshuLab Logo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the AshuLab mark — three nodes in a triangle whose top node forms the crossbar of an "A", on a round badge — as a vector master plus the PNG exports the login page, Discord server and favicon need.

**Architecture:** Concepts are generated as raster images so the operator picks by eye, then the chosen mark is drawn by hand as SVG. Hand-drawing is the point: a diffusion model produces soft edges and unreliable letterforms, and this mark must stay crisp at 32 px and survive Discord's circular crop.

**Tech Stack:** SVG (hand-authored), `rsvg-convert` for rasterisation, ImageMagick for the favicon sizes.

**Spec:** This document; design agreed in session 2026-11-14 (navy base, cyan accent, no text in the mark, separate lockup for the wordmark).

## Global Constraints

- Navy base `#0B1220`, cyan accent `#2DD4FF`. No magenta or orange — that palette belongs to Ashuvidz.
- The badge is square with a circular background so a circular crop loses nothing.
- No text, letters or numbers inside the badge.
- Every deliverable is produced from the SVG, never upscaled from a PNG.
- Line weights must survive a 32 px render: minimum 6 units of a 512-unit viewBox.

## Review Focus

- **32 px rendering:** a mark that looks fine at 512 px and turns to mush as a favicon is the most likely failure.
- **Circular crop:** Discord and authentik both clip to a circle; anything reaching the corners disappears.
- **Light background:** GitHub and the README are white, and a navy-only mark vanishes there.
- **Colour-blind readers:** cyan-on-navy and its monochrome form must keep the same silhouette.
- **Re-skinning for AshuFlex/AshuCraft:** the mark must work with the accent swapped and nothing else.

---

### Task 1: Concept renders

**Files:**
- Create: `assets/branding/ashulab/concepts/*.png`

- [ ] **Step 1:** Render three interpretations of the cluster-A (flat geometric, bold circuit-trace, monoline) and show them to the operator.
- [ ] **Step 2:** Operator picks one, or asks for a variation.

### Task 2: Vector master

**Files:**
- Create: `assets/branding/ashulab/ashulab-badge.svg`

- [ ] **Step 1:** Hand-draw the picked mark at `viewBox="0 0 512 512"`, circle background, three nodes, crossbar eyelets drawn as actual circles so they stay round at any size.
- [ ] **Step 2:** Render at 512 px and 32 px; confirm the A silhouette is still readable at 32 px.

### Task 3: Background variants

**Files:**
- Create: `assets/branding/ashulab/ashulab-badge-monochrome.svg`, `ashulab-badge-transparent.svg`

- [ ] **Step 1:** Produce a single-colour variant for white backgrounds and a transparent-background variant for the login page, where authentik supplies the backdrop.

### Task 4: Exports

**Files:**
- Create: `assets/branding/ashulab/png/ashulab-badge-{512,256,128,64,32}.png`

- [ ] **Step 1:** Rasterise each variant with `rsvg-convert` at the five sizes, then verify each file's real dimensions with `identify`.

### Task 5: Lockup (optional, after the badge is approved)

**Files:**
- Create: `assets/branding/ashulab/ashulab-lockup.svg`

- [ ] **Step 1:** Badge plus "AshuLab" wordmark on one line, for the authentik login page and the repository README.
