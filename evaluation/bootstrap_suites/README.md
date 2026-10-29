# G1 Bootstrap acceptance suites

This directory holds server-controlled reviewed release data. No FONST suite is approved.
Never accept question content, answers, thresholds, approval flags or paths from a validate request.

Each `<suite_id>/` contains `manifest.json` and `questions.jsonl`. Manifest fields:
`contract="g1-acceptance-v1"`, `suite_id`, `version`, `documents` (ordered frozen document scope:
`document_name`, `file_hash`, `document_version`). Questions must contain exactly 18 positives
and 2 negatives. Every row requires a stable unique `id`, nonempty `question`, explicit
boolean `expects_evidence`, `authored_by`, independent `reviewed_by`, ISO `reviewed_at`,
and `approved=true` after independent review.

Positives also require independent `fact_id`, `topic`, `reference_fact` and nonempty
`expected_citations`. Each citation requires `source_file`, positive integer `page_number`,
`file_sha256`, `document_version`, frozen `chunk_id`, `support_text`, `support_text_sha256`.
Support hashes use the project's canonical JSON SHA-256 (`stable_hash` on the string).
Support text must exist in the frozen chunk at the referenced page. Duplicate facts or
identical support spans cannot count twice. At least three document-derived topics are
required, each with at least three positive questions; every declared document is covered.

Negatives require empty citations, distinct questions, `negative_kind` values
`missing_detail` and `unsupported_assertion`, `rationale` and `checked_documents` equal
to the full declared corpus. The independent reviewer must inspect all original pages;
a retrieval miss does not prove absence. Reviewers must also check fact independence
and whether topics and questions actually represent the source. Automated checks cannot
replace that semantic review.

Package SHA is `stable_hash({"manifest": manifest, "questions_sha256": SHA256(raw JSONL bytes)})`.
Only after independent review, a controlled release/code-review process may register
`suite_id`, `sha256`, `approved_by`, `approved_at` in `approved_suites.json`. Client JSON
reviewer names alone confer no authority. Validate accepts only suite ID and expected package SHA.

Pass criteria remain 18 positives, 2 negatives, at least 15 positive matches and all
existing HTTP, trace, refusal and structural gates. A successfully promoted suite becomes
the complete regression scope; later candidates cannot remove questions by changing documents.
