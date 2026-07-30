# Gmail call-to-action monitoring, dashboard mirror, and fulfillment loop

Not a Python build step - this runs as two Claude scheduled tasks (not scripts
in `src/`), because Gmail is only reachable as an MCP connector tool inside a
live Claude session, the same constraint documented for boards.py (build step
4b). Streamlit itself never touches Gmail directly.

| | Scan | Fulfillment |
|---|---|---|
| **Task ID** | `panga-gmail-cta-scan` | `panga-cta-fulfillment` |
| **Schedule** | 4x/day - 8am, 12pm, 4pm, 8pm local (`7 8,12,16,20 * * *`) | every 10 minutes (`*/10 * * * *`) |
| **Job** | Find NEW inbox emails, classify, label, mirror call-to-action emails onto the dashboard, detect application confirmations | Execute what Zahir clicked on the dashboard (Dismiss / Draft reply), and notice when he's sent a draft himself |
| **Task file** | `C:\Users\User\.claude\scheduled-tasks\panga-gmail-cta-scan\SKILL.md` | `C:\Users\User\.claude\scheduled-tasks\panga-cta-fulfillment\SKILL.md` |

Both need Zahir to click **Run now** once each so unattended runs can use the
Gmail connector without pausing on a permission prompt.

## 1. Scan (`panga-gmail-cta-scan`)

- **State tracking:** Gmail labels `Panga/Reviewed` and `Panga/Call-to-Action` -
  no local database for email state. Visible directly in Zahir's inbox.
- **Classification buckets**, per thread:
  a. Not job-search-related -> skip entirely, unlabeled.
  b. Job-search-related but passive (job-alert digests, newsletters) -> label
     `Panga/Reviewed` only.
  c. A call-to-action (interview invite, assessment/task request, offer,
     rejection, recruiter asking a direct question) -> label both
     `Panga/Reviewed` and `Panga/Call-to-Action`, **and** mirror it onto the
     dashboard (§2 below).
  d. An application-received/confirmation email -> label `Panga/Reviewed`,
     then attempt an application match (§3 below).
- **Notification:** one push notification per run, combining any genuine
  call-to-action found and/or a count of new application-match suggestions.
  Silent if neither applies.
- **Safety:** this task only ever reads and labels. It never sends, replies
  to, or drafts a reply to anything on its own initiative - drafting only
  happens in the fulfillment task below, and only in direct response to
  Zahir clicking "Draft reply" himself.

### Application-match detection - built 2026-07-29

When an email looks like an application-received/confirmation (bucket d), the
task checks `tailoring.applications.load_applications()` for any job with
status "under review" and tries to match it by title/org/req number. A
confident match calls `applications.suggest_status(source, job_id, "applied",
reason)` - this does NOT change the real status. The Results screen shows
pending suggestions at the top with Confirm/Dismiss buttons
(`applications.confirm_status_suggestion()`); Zahir always makes the final
call, since matching an email to the right job is a best guess (e.g. two
identical-titled duplicate postings can't be told apart from an email alone).

## 2. Dashboard mirror - built 2026-07-29

Bucket (c) call-to-action emails are also written to a local JSON store
(`data/cta_emails/cta_emails.json`, via `src/tailoring/cta_emails.py`:
`add_cta_email()`), so Zahir sees them on Panga's own **Call to Action** page
in the Streamlit dashboard, not only as a push notification + Gmail label.
The page groups by category (offer, interview request, assessment/take-home
task, recruiter question, rejection) with a count per category, plus filter
and search. Each email has three actions: **Open in Gmail**, **Draft reply**,
**Dismiss**.

Because the dashboard process has no live Gmail access, Draft reply and
Dismiss don't touch Gmail directly - they write a request flag
(`request_draft()` / `request_archive()`) that the fulfillment task below
executes on its next pass. This keeps the dashboard responsive (the item
disappears from view immediately on Dismiss) while the real Gmail side effect
happens shortly after in the background.

## 3. Fulfillment loop (`panga-cta-fulfillment`) - built 2026-07-29

Zahir's intent: click Dismiss/Draft reply on everything on the Call to Action
page, then go check Gmail - by then dismissals should already be archived and
drafts should already exist; once he sends a draft himself, the dashboard
should notice and clear it without any further click from him. A single scan
running 4x/day was too slow a turnaround for that, so this is a **separate**
task running every 10 minutes while the Claude app is open:

1. **Archive fulfillment** - `get_pending_archive_requests()` -> for each,
   remove the `INBOX` label (archives it) and apply the `Panga/Handled` label
   (created 2026-07-29, `Label_4`), then `mark_archived()`.
2. **Draft fulfillment** - `get_pending_draft_requests()` -> for each,
   compose a short reply tailored to the email's category and actual content
   (live Claude reasoning each run, not a fixed template - same architectural
   pattern as `tailor.py`/`interview.py`: Python only orchestrates, Claude
   drafts the text), create a real Gmail draft via `create_draft()`
   (`replyToMessageId` set so it threads correctly, **never sent
   automatically**), then `mark_draft_created()`.
3. **Reconciliation** - `get_awaiting_draft_send()` -> compare each
   outstanding `draft_id` against Gmail's live `list_drafts` result. If a
   draft Zahir sent (or deleted) is no longer there, `mark_draft_sent()`
   resolves that dashboard item and queues its thread for archiving too, so
   Gmail and the dashboard stay consistent without Zahir clicking Dismiss for
   something he already handled by sending the reply.
4. Repeats step 1 once more at the end, to archive anything step 3 just
   queued in the same run rather than waiting another 10 minutes.
- **Notification:** silent on routine runs (every 10 minutes would be noise)
   - only pushes if something is actually broken (e.g. draft creation keeps
   failing, Gmail auth looks broken).

**Real limits to keep in mind:** both tasks only run while the Claude app is
open. The dashboard's Refresh button re-reads the local JSON file - it cannot
reach Gmail live itself (Streamlit has no MCP/Claude access) - so the picture
on screen is always as-of the last fulfillment run (up to ~10 minutes stale),
never truly instant.

## Prompts (reference copies - the live versions are the SKILL.md files above)

Since scheduled runs start fresh with no memory of any conversation, each
task prompt carries its own context (target roles, recruiter/ATS domains to
watch for, etc.) rather than assuming prior context. These are point-in-time
copies for version-control history - see the SKILL.md files for exact current
wording.

### `panga-gmail-cta-scan` (current as of 2026-07-29)

```
You are a scheduled monitoring run for Zahir Uddin's job search, checking his Gmail (malikzahiruddin328@gmail.com). Zahir is a CIO / Head of IT (25+ years, primarily life sciences/pharma - GxP, CSV, 21 CFR Part 11 - but open cross-industry), jobless since Jan 2026, actively searching. This is a fully automated run with NO memory of any prior conversation - everything you need is in this prompt.

GOAL: find NEW emails in his inbox that are job-search-related AND require his attention (a "call to action"), and notify him only if you find at least one. Also: detect application-confirmation emails and try to match them to a specific job record so Zahir doesn't have to remember to mark "applied" himself. This is his normal personal inbox, not a job-only mailbox, so be precise, not over-inclusive.

STEPS:
1. Call the Gmail connector's search_threads tool with query: `-label:Panga/Reviewed -in:spam -in:trash newer_than:2d in:inbox`
2. For each thread, use the subject/sender/snippet (call get_thread for the full content if it's ambiguous) and classify into exactly one bucket:
   a. NOT job-search-related (personal/unrelated) -> skip entirely, do not label at all.
   b. Job-search-related but passive, no action needed (job-alert digests, newsletters) -> apply label "Panga/Reviewed" only.
   c. Job-search-related AND a call-to-action (interview invite/scheduling request, assessment or take-home task request, a job offer, a rejection, or a recruiter asking a direct question / requesting a reply or call) -> apply BOTH "Panga/Reviewed" AND "Panga/Call-to-Action", then also do STEP 3B below so it shows up on the Panga dashboard, not just as a Gmail label.
   d. An APPLICATION-RECEIVED/CONFIRMATION email (e.g. "thank you for applying to X", "we received your application") -> apply label "Panga/Reviewed", then do STEP 3 below.
   If the labels "Panga/Reviewed" or "Panga/Call-to-Action" don't exist yet (check with list_labels), create them first with create_label, then apply with label_thread.
3. FOR BUCKET (d) ONLY - try to match to a specific job so Zahir doesn't have to remember to mark it "applied": using the venv Python at Panga\venv\Scripts\python.exe with sys.path including Panga\src, call tailoring.applications.load_applications() and find any record with status "under review". Compare the email's job title/organization/req-number against those records. If you find a clear, confident match (not a guess - if the email doesn't give enough detail to distinguish between multiple "under review" jobs, don't match), call tailoring.applications.suggest_status(source, job_id, "applied", "<one-sentence summary of what the email said>"). This does NOT change the real status - Zahir confirms it himself later. If no confident match, do nothing further for that email (it's still labeled Reviewed from step 2).
3B. FOR BUCKET (c) ONLY - mirror it to the Panga dashboard so Zahir sees it there, not just via the push notification and Gmail label: using the same venv Python (Panga\venv\Scripts\python.exe, sys.path including Panga\src), call tailoring.cta_emails.add_cta_email(thread_id, subject, sender, snippet, date, category), where category is one of "rejection", "interview_request", "assessment_request", "offer", "recruiter_question" (pick the closest fit). date should be the message's ISO timestamp from the thread data. This just mirrors the email for display on the Results screen - it never changes Gmail or any application status.
4. NEVER send, reply to, or draft a reply to any email. Read and label only - no exceptions.
5. Build a notification: combine (a) any bucket (c) call-to-action threads found this run, most time-sensitive first, and (b) whether any new application-match suggestions were created in step 3 (count only). If either applies, send exactly ONE PushNotification (status "proactive"), under 200 characters. Example: "3 job replies need you: interview req from Acme (CIO role), rejection from BioCo. Also: 1 application match ready to confirm." If neither applies, send NO notification - routine runs with nothing noteworthy should stay silent.
6. When genuinely unsure if something is job-search-related at all (could be spam or unrelated), skip it entirely and leave it unlabeled for Zahir to judge himself, rather than guessing.

CONTEXT TO CLASSIFY ACCURATELY: Zahir targets CIO / Head of IT / SVP / VP / Director roles, primarily life sciences/pharma but open cross-industry (finance, media, energy, insurance - his background spans AbbVie, Eisai, TD Bank, Great American Financial, Univision, EMC/BP/Ethicon-J&J/The Hartford). He's actively searching via USAJOBS.gov, ZipRecruiter, and Dice, and has researched (but not necessarily applied through yet) pharma-specific boards and recruitment firms including Planet Pharma, BioSpace, RAPS Career Center, FierceBiotech Jobs, Life Search Technologies, TSP Life Sciences, Frontline Source Group, Slone Partners, and others, plus Lensa as a general aggregator. Recruiter/ATS domains to watch for: greenhouse.io, lever.co, myworkday.com, icims.com, smartrecruiters.com, taleo.net, successfactors.com, hirevue.com, calendly.com (interview scheduling), plus direct recruiter/company domains and the boards above.
```

### `panga-cta-fulfillment` (created 2026-07-29)

```
This is an automated run for Zahir Uddin's job-search tool "Panga" (repo at C:\Users\User\Desktop\Myra\Panga). No memory of prior runs - everything needed is here. Zahir is a CIO/Head of IT job-searching since Jan 2026, primarily life sciences/pharma but open cross-industry.

CONTEXT: Panga's Streamlit dashboard has a "Call to Action" page listing job-search emails the separate panga-gmail-cta-scan task flagged (interview requests, offers, assessment requests, recruiter questions, rejections). Because the dashboard (Streamlit) has no live Gmail access, its Dismiss and Draft reply buttons don't touch Gmail directly - they just write a request into a local JSON file (data/cta_emails/cta_emails.json, managed by src/tailoring/cta_emails.py). THIS task is the only thing that actually executes those requests against Gmail, plus checks whether Zahir has since sent a draft this task created. Zahir's expectation: he clicks buttons on the dashboard, then goes to check Gmail - by then dismissed threads should already be archived and drafts should already exist; and when he later sends a draft himself, the dashboard should notice and clear it without him having to click anything else.

Use the venv Python at C:\Users\User\Desktop\Myra\Panga\venv\Scripts\python.exe with sys.path including C:\Users\User\Desktop\Myra\Panga\src to call functions in tailoring.cta_emails (run short python -c snippets, or a scratch script, via Bash/PowerShell).

STEP 0 - Label lookup: call the Gmail connector's list_labels tool and find the label named "Panga/Handled" (it already exists - if for some reason it's missing, create it with create_label). Note its labelId for step 1.

STEP 1 - Fulfill archive (Dismiss) requests: call tailoring.cta_emails.get_pending_archive_requests(). For each returned item (has thread_id): call unlabel_thread(threadId, ["INBOX"]) to archive it out of the inbox, then label_thread(threadId, [the "Panga/Handled" labelId]). Then call tailoring.cta_emails.mark_archived(thread_id). If a call errors (e.g. thread already archived), don't let it stop the rest - move to the next item, note it, continue.

STEP 2 - Fulfill draft (Draft reply) requests: call tailoring.cta_emails.get_pending_draft_requests(). Each item has subject, sender, snippet, category, message_id, thread_id. For each:
  a. Extract a plain email address from the "sender" field (it may be formatted like "Name <email@domain.com>" - use just the email@domain.com part; create_draft's `to` field rejects the "Name <email>" format).
  b. Compose a short, professional reply body yourself (2-4 sentences) tailored to the category and the actual subject/snippet content - this is real reasoning, not a fixed template:
     - offer: express genuine interest/thanks, ask about next steps (start date, comp details if not covered, etc).
     - interview_request: confirm enthusiasm and general availability, ask them to propose times (don't invent a specific date/time you don't have).
     - assessment_request: acknowledge receipt, confirm you'll complete it, ask about the deadline if unclear.
     - recruiter_question: answer helpfully based on what's known about Zahir (CIO/Head of IT, 25+ years, life sciences/pharma-heavy background - AbbVie, Eisai, TD Bank, Great American Financial, Univision, EMC/BP/Ethicon-J&J/The Hartford - open cross-industry) if the question is answerable from that; otherwise keep it brief and ask a clarifying question back.
     - rejection: brief, gracious thank-you, express interest in being considered for future roles.
     Sign off as Zahir.
  c. Call create_draft(to=[extracted_email], subject="Re: " + subject, body=composed_text, replyToMessageId=message_id).
  d. Take the returned draft id and call tailoring.cta_emails.mark_draft_created(thread_id, draft_id).
  If sender parsing fails or the address looks clearly automated/unmonitored (e.g. contains "noreply" or "no-reply") still create the draft as instructed - Zahir reviews everything before sending, that's the safety net, not this task second-guessing him.

STEP 3 - Reconcile sent drafts: call tailoring.cta_emails.get_awaiting_draft_send() to get items with a draft_id still considered outstanding. If the list is non-empty, call the Gmail connector's list_drafts tool (paginate with pageToken until you've seen all drafts, view=DRAFT_VIEW_METADATA_ONLY is enough) and collect the current set of draft ids. For each outstanding item whose draft_id is NOT in that current set (meaning Zahir sent it or deleted it), call tailoring.cta_emails.mark_draft_sent(thread_id).

STEP 4 - Second archive pass: mark_draft_sent() in step 3 flags newly-resolved threads for archiving too (so Gmail's inbox and the dashboard agree). Re-run STEP 1's logic once more (get_pending_archive_requests / unlabel_thread+label_thread / mark_archived) to pick up anything step 3 just queued, so it's archived in this same run instead of waiting for the next one.

STEP 5 - Notification: stay silent on routine runs (this runs every 10 minutes - a notification every time would be noise). Only send a PushNotification (status "proactive", under 200 chars) if something went wrong that Zahir needs to know about (e.g. a draft repeatedly fails to create, Gmail auth looks broken) - not for normal successful fulfillment, even when items were processed.
```
