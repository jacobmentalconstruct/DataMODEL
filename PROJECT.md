# Project Definition

This document defines the product this repository is intended to become.

At project creation it is intentionally incomplete.

Do not infer or invent missing product decisions. Resolve them with the user.

---

## 1. Project Identity

**Project name:**  
_To be established._

**Short description:**  
_What is this project?_

**Purpose:**  
_Why should this project exist?_

---

## 2. Intended User

**Primary user or participant:**  
_To be established._

**Primary use context:**  
_To be established._

Do not invent personas or markets unless the user defines them.

---

## 3. Problem / Need

What problem, capability gap, or desired experience is this project intended to address?

_To be established with the user._

---

## 4. Intended Product

Describe the product from the outside.

What should exist when this project is meaningfully realized?

_To be established._

---

## 5. Primary Capabilities

What should a user actually be able to do?

_To be established._

Keep these capability-oriented rather than implementation-oriented.

---

## 6. Product Boundaries

### In Scope

_To be established._

### Explicitly Out of Scope

_To be established._

### Deferred / Maybe Later

_To be established only where useful._

---

## 7. Project-Specific Constraints

Record constraints that belong to this project rather than the reusable project-definition framework.

Examples may include:

- target platform;
- language or runtime constraints;
- local-only or networked operation;
- storage constraints;
- deployment assumptions;
- compatibility requirements;
- external systems that must or must not be used.

_To be established._

Do not duplicate generic architectural or workflow guidance already defined elsewhere.

---

## 8. User Experience Expectations

Describe important qualities of the product's behavior or interaction model.

Examples:

- desktop application;
- command-line tool;
- background service;
- GUI plus agent interface;
- local-first workflow;
- interactive or batch behavior.

_To be established._

---

## 9. Completion Condition

What concrete state would make the user say:

> "Yes, this project now exists as the thing I intended to build."

_To be established._

This should describe meaningful product capability, not merely code completion.

---

## 10. Known Unknowns

Record unresolved product questions that materially affect planning.

_To be established._

Do not manufacture questions merely to fill this section.

---

## 11. Definition Status

The current status is recorded once, under **Current Decision** below. Use one of:

- **UNDEFINED** — product intent has not yet been sufficiently established.
- **DEFINING** — the builder and user are actively defining the project.
- **PROPOSED** — a coherent project definition exists but has not yet been accepted.
- **DEFINED** — the user has accepted the current project definition.
- **REVISING** — established product intent is being intentionally reconsidered.

---

## 12. Planning Gate

`PLAN.md` may be explored while the project is being defined, but implementation planning must not be treated as approved until this document is coherent enough to answer:

- What are we building?
- Why does it exist?
- What should the user be able to do?
- What is outside the intended product?
- What project-specific constraints matter?
- What would count as meaningful completion?

If those answers are still materially unresolved, remain in product definition.

---

## Current Decision

**Definition status:** UNDEFINED

**Next action:**  
Work with the user to establish the project identity, intended product, primary capabilities, boundaries, constraints, and completion condition.

Implementation permission is decided in `PLAN.md` (**Current Decision**), and requires this definition to satisfy the planning gate above.