# Stakeholder Analysis

## Summary

The PRD frames Epic 2 as a pure developer-library concern: "Who: Developers building and operating the pointy-rag system." This framing is too narrow. Four unstated stakeholder groups — downstream epic teams, operations/infrastructure, end users of the RAG system, and a security/compliance function — all have requirements that the PRD does not address. Of these, the downstream epic teams (Epic 3 and Epic 4) are the most critical gap: they are the primary consumers of the APIs being designed here, and several open questions (Q2, Q3, Q5) are proxy questions that really belong to those teams, not the Epic 2 implementors.

The PRD also has a mild but real conflict between the needs of the Epic 2 polecats (who want clean, independent components) and Epic 3/4 (who need a stable, well-specified API contract before they can build). Several design decisions that are being deferred as Epic 2 "implementation details" will land as breaking-change constraints on Epic 3/4. The PRD should designate an owner for those cross-epic API decisions rather than leaving them to each polecat.

## Findings

### Critical Gaps / Questions

**1. Epic 3 (progressive disclosure) is an unnamed co-designer, not just a consumer**
- The progressive disclosure layer directly depends on `run_agent` (agent wrapper) and `chunk_markdown` (chunker). Epic 3 will define its own prompts, parse the structured JSON output, and rely on chunk metadata to build disclosure trees.
- Why this matters: Open Questions 2, 3, and 5 in the PRD are effectively Epic 3 design questions delegated to Epic 2 implementors. If polecats resolve these differently from what Epic 3 needs, Epic 3 faces breaking changes before it even starts.
- Clarifying question: Has the Epic 3 designer signed off on the `run_agent` output schema, the `allowedTools` decision, and the chunk metadata shape? If not, who owns that cross-epic API contract?

**2. Epic 4 (pipeline orchestration) has unstated requirements that drive error handling design**
- Epic 4 will wire all four Epic 2 components into a pipeline. Partial failure behavior (what happens when batch 2 of 4 fails permanently), error contract (typed exceptions vs. bare Exception), and the chunker's "total" guarantee all directly affect how Epic 4 can be written.
- Why this matters: If Epic 4 must handle three different error contracts per component, the pipeline code becomes complex and fragile. Open Question 5 (error handling philosophy) is really an Epic 4 usability question.
- Clarifying question: Does the Epic 4 pipeline designer have a preference for a unified error contract? Should they be consulted before Open Question 5 is closed?

**3. No operations or infrastructure stakeholder is identified**
- The PRD has no mention of how these components run in production: containerization, resource limits, or deployment environment. PDF processing (pymupdf) and EPUB processing (ebooklib) can be memory-intensive for large files. The agent wrapper spawns subprocesses.
- Why this matters: An ops team that inherits this system has no observability into failures. There are no logging requirements, no metrics hooks, no alerting on embedding failures or conversion timeouts. Silent failures are possible (e.g., the fallback extractor produces degraded output with no signal that it was used).
- Clarifying question: Is there an ops/infrastructure owner for this system? What observability (logs, metrics, alerts) do they need from these components?

**4. Security team has unstated requirements on the agent wrapper**
- The agent wrapper spawns Claude Code as a subprocess. Open Question 2 asks what `--allowedTools` to grant the conversion agent. If the agent has `Write` access, a malicious or hallucinating prompt could overwrite arbitrary files on the host system. This is a security boundary decision.
- Why this matters: The security posture of the deployment environment depends on this decision. A sandboxed Claude invocation (no tools, stdin only) is fundamentally safer than one with filesystem write access.
- Clarifying question: Is there a security owner who should sign off on the `--allowedTools` configuration for the conversion agent? What is the threat model for documents processed through Claude?

### Important Considerations

**5. End users of the RAG system are entirely absent**
- The PRD's "Who" section names only "developers building and operating the system." But end users who query the RAG system are the ultimate consumers of ingestion quality. Poor chunking leads to poor retrieval leads to poor answers.
- Why this matters: Without end-user quality requirements, there is no minimum acceptable bar for chunking quality, conversion fidelity, or embedding accuracy. The PRD allows a fallback extractor that produces "possibly lower quality" markdown with no floor definition.
- This is not a blocker for Epic 2, but should be escalated: who owns the end-user quality story? That owner should define quality acceptance criteria before the ingestion pipeline (Epic 4) is designed.

**6. Voyage AI as a third-party stakeholder**
- The embedding client depends on Voyage AI's `voyage-4-lite` model. The PRD hardcodes this model and specifies a batch limit of 128 (an API constraint). There is no mention of rate limits, quota management, or what happens when the Voyage API is unavailable (vs. transient errors).
- Why this matters: The ops team needs a Voyage AI account, quota monitoring, and possibly a cost center. The per-batch retry handles transient errors but not sustained outages. There is no fallback if Voyage AI is down.
- Clarifying question: Who manages the Voyage AI account and monitors quota usage? Is there a fallback embedding strategy if Voyage AI is unavailable?

**7. Anthropic (Claude Code) as a third-party stakeholder**
- The agent wrapper invokes Claude Code CLI as a subprocess. Licensing, rate limits, and pricing for Claude Code are not mentioned. If this runs in CI or production at scale, the number of Claude invocations matters.
- Why this matters: The conversion agent is on the hot path for document ingestion. Cost and rate limiting are unaddressed. If this system ingests thousands of documents, the Claude invocation cost is non-trivial.
- Clarifying question: Is there a Claude Code usage budget or rate limit policy? Who manages the Anthropic account for this system?

**8. Support team has no post-launch runbook**
- When a document fails to convert (agent timeout, fallback produces garbage), there is no support path described. The converter silently falls back to lower quality output — support teams would see user complaints about poor retrieval without knowing which documents were affected or why.
- Why this matters: A document that silently degrades to fallback extraction is indistinguishable from a document that converted successfully. Operations and support need a way to audit conversion quality and identify documents needing reprocessing.
- Suggested: Add a requirement that conversion records (success, fallback used, agent timeout) are logged at minimum to a structured log that ops can query.

### Observations

**9. Compliance/legal requirements are absent but may apply**
- Documents being processed may contain PII (e.g., customer records in PDF form), confidential business information, or content subject to data residency requirements. The agent wrapper sends document content to Claude Code as a subprocess, and embedding generation sends text to Voyage AI's API.
- For a developer-internal tool, this may be acceptable. But if the system ever processes customer data, compliance requirements (GDPR, SOC2, HIPAA) would apply to both the Claude and Voyage AI API calls.
- This is not a blocker but should be flagged before the system is opened beyond developer use.

**10. Launch coordination is unspecified**
- Epic 3 and Epic 4 both depend on Epic 2's API contracts being stable before they can start. The PRD does not identify who needs to be notified when Epic 2 closes, or what artifact (interface spec, typed stubs) formally constitutes the handoff.
- A simple `protocol.md` or typed stub file describing the public API surface (function signatures, exception types) would reduce integration risk for Epic 3/4 polecats.

**11. The developer (polecat) as a DX stakeholder**
- The PRD says "component independence" is a goal, allowing four polecats to build in parallel. This is a valid developer-experience decision. However, the open questions (Q1–Q5) are currently unresolved, meaning each polecat must either decide locally or block on answers. Local decisions on shared API surface (JSON schema, error types) will produce inconsistent results.
- Suggest: resolve Q2, Q3, Q5 as a single cross-cutting decision before dispatching polecats. This prevents four independent polecats from making four incompatible choices.

## Confidence Assessment

**Medium.** The PRD is buildable for all four components in isolation. The component independence claim holds for implementation but not for API contract. The major stakeholder gap is the absence of Epic 3/4 designer input on the cross-component API surface — this is the highest-risk unstated stakeholder. Ops, security, and end-user quality requirements are absent but not immediate blockers for Epic 2 dispatch. The compliance observation is low-risk given the current developer-internal scope. Overall PRD health on the stakeholder dimension is medium: the right implementors are identified but the right reviewers (Epic 3/4 designers, security, ops) are not in the loop.
