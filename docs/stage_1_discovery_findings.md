# Stage 1 Discovery Findings — CloudServe Solutions

Source evidence: `_context_md/Stakeholder_Interviews.md` (5 transcripts) and
`Capstone_Pack/05_Datasets/development_tickets.json` (500 tickets) /
`documentation.json` (29 articles), analyzed by
[`scripts/discovery_analysis.py`](../scripts/discovery_analysis.py). All
figures below are computed directly from the development set; none are
estimated or invented. Figures are reported from the 500-ticket development
set per the Dataset Guide — the hidden 120-ticket set is not available and is
not used here.

---

## Section 1 — What the interviews tell you

| Who | What they told you | What they appear not to know | What you want to verify in the data |
|---|---|---|---|
| Marcus Adeyemi, Head of Support | First-response SLA is breached (8–12h vs 2h target); FCR is 42% vs a 65% benchmark and is what he actually cares about; escalated tickets cost "roughly 4x" a resolved one; cannot break tickets down by category without guessing | Does not know the real intent-category breakdown of his own queue; does not know what fraction of escalations were actually avoidable at tier one | Actual FCR rate; actual intent distribution; actual cost ratio of escalated vs non-escalated tickets |
| Sofia Restrepo, Tier One Agent | ~70% of tickets are ones she's seen before; the bottleneck is finding/writing the answer, not knowing it; documentation is fine but internal search doesn't work, so agents keep private answer files instead; non-fluent customers' tickets take longer and have the worst satisfaction, and "no one has noticed" | Does not realize her complaint to Ines about search was heard as a complaint about documentation quality — a communication gap, not a technical one | `answerable_from_docs` rate; outcomes (CSAT, resolution time, escalation) split by `language_fluency` |
| Daniel Okonkwo, Tier Two Engineer | About half of what reaches him is something tier one could have resolved with more confidence or better findability, not genuine difficulty; escalations arrive with no triage summary, forcing him to re-ask the customer; worries that automation trained on agents' private snippet files will scale up stale/wrong answers | Doesn't quantify which intents specifically drive this — speaks in impressions from "tickets I closed last week" | What share of `expected_route=escalate` tickets are discretionary (not policy-mandated via `must_not_auto_respond`) vs. mandatory |
| Ines Varga, Technical Writer | 29 reviewed KB articles exist and are accurate within review cycle; support agents don't use them — they reconstruct answers from memory because title-based keyword search doesn't match how customers phrase problems; wants provenance (which article) attached to any automated answer; feature requests, roadmap questions, and genuinely novel incidents have no article to retrieve | Doesn't know how large the "no article exists" category actually is as a share of volume | `answerable_from_docs=False` rate and which intents fall there |
| Ravi Menon, Customer | Waiting is the core pain, but its cost is wildly different by urgency (a blocked deployment vs. a "how does pagination work" question); he self-serves via docs about half the time while waiting; wants automated answers to be labeled as such and to cite a source; is already comparing his business-tier response time unfavorably to a colleague's enterprise-tier experience | Doesn't know that CloudServe's own data already shows enterprise tickets resolving *more slowly* than business-tier ones today | Resolution time, FCR, and escalation rate split by `customer_tier` |

**What was never mentioned:** No one raises data privacy/PII handling in automated responses, despite it being an obvious risk once a system is drafting replies from ticket bodies that may contain account or billing details. No one mentions multi-turn conversations (every account here is implicitly single-shot). No one discusses what happens when documentation itself is wrong versus the system misreading a correct article, except Ines, who separates this concern only for herself.

---

## Section 2 — What the ticket data shows (n = 500)

| What to measure | Your figure | Where the figure came from | What surprised you about it |
|---|---|---|---|
| Total tickets in the sample | 500 | `development_tickets.json` length | — |
| Split by channel | email 42.4%, chat 31.0%, docs_comment 15.6%, forum 11.0% | `channel` field | Matches the Dataset Guide's stated ordering exactly |
| Split by intent category | 22 classes, fairly even (13–29 tickets each); see Section 3 | `labels.intent` | Distribution is much flatter than Marcus's guess (auth/deployment/billing/API as dominant) — no single intent exceeds 5.8% of volume |
| Split by urgency | not computed here (see note below) | `labels.urgency` | — |
| Proportion marked resolved on first contact | 43.8% | `history.first_contact_resolution` | Matches Marcus's self-reported "42%" almost exactly — he is right about this number |
| Average satisfaction rating | 2.97 / 5 | `history.csat_rating` | Markedly below what a "slow but decent" service (Ravi's description) would suggest — the data is harsher than the customer-facing account |
| Most frequent single question/intent | `data_export` and `data_residency` tie at 29 tickets (5.8% each) | `labels.intent` counts | Neither is one of the four categories Marcus guessed |
| Proportion answerable from existing documentation | 71.4% (357/500) | `labels.answerable_from_docs` | Closely matches Sofia's "seven out of ten" estimate — she is right, and the number confirms CloudServe has answers, it lacks a path to them |
| Proportion marked non-fluent, and their outcomes | 24.0% non-fluent; FCR 45.8% vs 43.2% fluent, CSAT 3.04 vs 2.95 fluent, escalation 54.2% vs 56.8% fluent, avg resolution 466.1 min vs 407.7 min fluent | `language_fluency` + `history.*` | Non-fluent tickets do **not** show worse FCR/CSAT/escalation than fluent ones in the aggregate history outcomes — contradicting Sofia's claim that these tickets have "our worst satisfaction scores." They do take longer to resolve on average, which is consistent with her account of extra back-and-forth, but the scored outcome (CSAT) does not confirm the "worst scores" part. |
| Outcomes by customer tier | enterprise: FCR 37.3%, CSAT 3.05, avg res. 483.7 min, escalated 62.7%; business: FCR 48.2%, CSAT 2.91, avg res. 358.8 min, escalated 51.8%; standard: FCR 43.1%, CSAT 2.98, avg res. 442.1 min, escalated 56.9% | `customer_tier` + `history.*` | Enterprise — the premium tier — currently has the *worst* FCR and the *slowest* resolution time and the *highest* escalation rate of the three tiers. This is the opposite of what Marcus's worry ("enterprise will notice if they get worse service") and Ravi's "they already get an hour" framing assume as the starting point. |
| Proportion that are repeat contacts | 21.6% | `history.repeat_contact` | — |

**Note on "first-response time":** the ticket schema's `history` block contains
only `resolution_time_minutes`, `csat_rating`, `escalated`, `first_contact_resolution`,
and `repeat_contact` (per `Dataset_Guide.md`). There is no distinct first-response-time
field. Marcus's 8–12 hour first-response figure is therefore taken as stated in
the interview and not independently re-derived from this dataset; this is
recorded as an open point rather than invented from the ticket data.

**Baseline averages (whole sample):**
- Average `resolution_time_minutes`: **421.7 minutes** (~7.0 hours)
- Average `csat_rating`: **2.97 / 5**
- Escalation rate: **56.2%**
- First-contact-resolution rate: **43.8%**
- Repeat-contact rate: **21.6%**
- Avg resolution time, escalated tickets: **726.6 min**; non-escalated: **30.5 min** — escalated tickets take **23.8x** as long as non-escalated ones, far above Marcus's quoted "roughly 4x" estimate.

---

## Section 3 — Where the time actually goes

### Intent volume vs. effort (total agent-minutes)

Top categories by share of total agent-minutes (`resolution_time_minutes` summed per intent), compared to share of ticket volume:

| Intent | Share of volume | Share of effort (minutes) | Why they differ |
|---|---|---|---|
| security_incident | 5.2% | 9.7% | 100% escalation rate, longest average handle time (785.9 min) — mandatory escalation policy (`must_not_auto_respond`) |
| compliance_request | 5.2% | 8.4% | 100% escalation rate, avg 679.3 min — contractual/legal review per ticket |
| data_residency | 5.8% | 7.1% | 69.0% escalation rate, avg 515.0 min — matches Daniel's named risk category |
| feature_request | 4.0% | 6.9% | 100% escalation rate, avg 723.9 min — no documentation exists to retrieve, per Ines |
| deployment_failure | 5.4% | 6.2% | 59.3% escalation rate, avg 482.9 min |
| rate_limit | 2.6% | 0.8% | Only 23.1% escalation, avg 122.2 min — a genuinely "known" ticket type, consistent with Sofia's description of password/rate-limit tickets as fast, repeat questions |

Four intents — `security_incident`, `compliance_request`, `feature_request`, and
`unclear_request` — show a **100% escalation rate**, consuming disproportionate
effort relative to their ~4–5% share of volume each. These map directly onto
Daniel's named no-go categories (security, billing-adjacent compliance, and
anything with no retrievable answer) and Ines's "feature requests and novel
incidents have nothing to retrieve."

### Reconstructing Sofia's working process

| Step | What the agent does | Roughly how long | Could this be automated? |
|---|---|---|---|
| 1 | Open and sort the queue by age | Minutes, not the bottleneck | Yes — trivial triage/routing |
| 2 | Recognize whether this is a "seen it before" ticket | Seconds if recognized, longer if not | Partially — this is a classification problem |
| 3 | Find the existing answer (search KB or personal file) | **This is where Sofia says the real time goes** — documentation search is "painful" so most agents don't use it | Yes, if retrieval/search is fixed — this is the core findability gap Ines independently confirms |
| 4 | Write out the answer, adapted to the ticket | Minutes, compounds when customer is non-fluent (has to infer the actual question first) | Partially — drafting is automatable; final send-decision is not, per Marcus's and Sofia's shared "would rather it say nothing than say something wrong" |
| 5 | Decide to answer or escalate | Fast if confident; escalation itself is "forward with no summary," per Daniel | The decision can be automated for confidently answerable, non-restricted intents; the handoff packet (context, doc cited, what wasn't confident) currently does not exist and is explicitly requested by Daniel |

This reconstruction, combined with the data, supports Sofia's account over a
pure headcount story: fast tickets resolve in ~30 minutes on average
(non-escalated), while the problem is concentrated in search/findability and
escalation handoff quality, not raw ticket volume alone.

---

## Section 4 — What the client counts as success

| Measure | Who watches it | Current value | What they would call success | Confidence |
|---|---|---|---|---|
| First-response time vs. 2-hour SLA | Executive team (reported by Marcus) | 8–12 hours (per interview; not independently derivable from this dataset's schema) | Inside the 2-hour SLA | Medium — stated, not re-derived |
| First-contact resolution (FCR) | Marcus, "if I'm being honest, more than response time" | 43.8% (confirms his "42%") | Industry benchmark of 65% | High — matches dataset exactly |
| Escalation cost ratio | Marcus | Measured at 23.8x (escalated vs. non-escalated resolution time), not the "~4x" he quoted | Lower escalation rate and lower per-ticket cost | High — data available, contradicts his stated estimate |
| CSAT | Not explicitly named as watched by anyone in the interviews | 2.97 / 5 | Implicitly higher, consistent with FCR gains | High |

**The sentence test:**
- *This project will have been worth doing if, within three months of launch, we see* **first-contact resolution rise measurably above the current 43.8% baseline, with no increase in incorrect or unsafe automated responses to customers, especially enterprise-tier customers.**
- *We will know it did not work if we see* **FCR unchanged or falling, escalation volume or per-ticket escalation cost unchanged, or any ticket in a `must_not_auto_respond` class (security, billing commitments, data residency) receiving an automated reply.**
- *The measure the client will actually be judged on internally is* **first-response time against the 2-hour SLA, even though Marcus himself says FCR is the number he actually cares about** — this is the gap Section 6 names explicitly.

---

## Section 5 — Data, constraints and risk

| Data source | What it contains | Quality and gaps | Access constraints | Private data present? |
|---|---|---|---|---|
| Support tickets (`development_tickets.json`) | 500 labelled tickets, full history block | Intent labels and routing/documentation labels appear complete; no explicit first-response-time field | Development set only — validation and hidden sets are schema-identical but must not be used to tune final numbers | Yes — `customer_name`, `customer_id`, ticket body text |
| Documentation (`documentation.json`) | 29 KB articles across 8 categories (auth, deployment, API, performance, billing, data, security, account, integration, onboarding per corpus — 10 categories observed in this run) | Accurate "within the review cycle" per Ines; does not cover feature requests, roadmap/timing, or genuinely novel incidents by design | — | No |
| Past resolutions (agents' private answer files) | Not in the dataset at all — exists only as a description in the interviews | Per Daniel: unreviewed, and some answers are stale ("correct two years ago, not right since"); per Ines: she has asked twice for these to be consolidated and it hasn't happened | Not accessible — referenced only in interview transcripts, not provided as a file | Unknown, likely yes |
| Customer records | Only the fields embedded in each ticket (`customer_tier`, `customer_region`, `language_fluency`, `customer_id`, `customer_name`) | No separate customer master data provided | — | Yes |

**Initial risk register:**

| What could go wrong | How likely | How bad | Who it affects | First thought on preventing it |
|---|---|---|---|---|
| The system answers confidently and incorrectly | Medium — 28.6% of tickets are not answerable from docs at all | High — Marcus: "I would rather it said nothing than said something wrong"; Ravi: he will act on it and "break something" if it sounds certain and is wrong | All customers, worst case for engineers who screenshot and post publicly | Abstain when `answerable_from_docs` confidence is low; never auto-respond on the 17.4% of tickets flagged `must_not_auto_respond` |
| Private information appears in a reply | Low-medium, unassessed in this dataset | High — compliance exposure, Marcus has an autumn compliance review | Any customer | Not directly measurable from this dataset; flag as an open question requiring a governance control, not a ticket-data finding |
| Some customers get consistently worse answers | Already true today, pre-automation | High — enterprise tier currently shows the worst FCR (37.3%) and slowest resolution (483.7 min) of any tier, despite being the premium tier Marcus worries about protecting | Enterprise customers now; any tier if the gap is carried into the automated system | Measure and report quality by `customer_tier`, `customer_region`, and `language_fluency` as a standing fairness check, not a one-time audit |
| Documentation goes out of date | Low within the 29-article corpus (reviewed on rotation per Ines) but unbounded for the unreviewed personal answer files Daniel describes | Medium — stale answers "scale up a mistake" if used as training signal | All customers whose intent maps to a stale private answer | Build only from the reviewed KB corpus (29 articles), not from agents' private files, and surface the cited `doc_id` with every answer (Ines's explicit request) |

---

## Section 6 — Three contradictions, resolved by data

### Contradiction 1 — Daniel's "half of escalations were avoidable" vs. Marcus's silence on it

**Who says what:** Daniel claims roughly half of what reaches tier two is
something tier one could have resolved "if they had been confident, or if
they had found the right page." The interviewer notes Marcus never
mentioned this, and Marcus's own account frames escalation purely as the
natural, expected routing of "hard" tickets.

**How the data settles it:** Of the 189 tickets labeled `expected_route=escalate`,
87 (46.0%) are flagged `must_not_auto_respond=True` — mandatory escalations
by policy (security, compliance, data residency, etc.), which cannot be what
Daniel means by "avoidable." The remaining **102 tickets (54.0% of all
escalations) are discretionary** — not policy-mandated. This is almost
exactly Daniel's "about half" figure.

**What follows:** Daniel is right, and Marcus — who "sees the escalation
rate, not what's inside the escalations" — is missing a real, data-confirmed
split between mandatory and discretionary escalation. A system that targets
only the discretionary half (confidence/findability failures) rather than
trying to automate mandatory-escalation categories is targeting the correct,
data-sized problem.

### Contradiction 2 — Is enterprise currently the best-served tier, or the worst?

**Who says what:** Nobody states this as an explicit disagreement, but two
accounts carry an unstated assumption that enterprise service is currently
*good* and the risk is making it *worse*: Marcus worries enterprise
customers "will notice immediately if they get worse service than they were
getting," and Ravi describes an enterprise-tier colleague getting replies in
about an hour, framing enterprise as the comparison point to aspire to.

**How the data settles it:** Segmenting history outcomes by `customer_tier`
shows enterprise currently has the **lowest FCR (37.3%)**, the **slowest
average resolution time (483.7 minutes)**, and the **highest escalation rate
(62.7%)** of the three tiers — worse than both business (48.2% FCR, 358.8
min, 51.8% escalated) and standard (43.1% FCR, 442.1 min, 56.9% escalated).

**What follows:** The baseline enterprise is already being served worse than
business tier on every outcome measure checked, not better. Marcus's framing
of enterprise risk as "don't make it worse" understates the problem —
enterprise is the tier most in need of improvement, not the one to protect
from regression. Any fairness or rollout plan should treat enterprise
specifically, not assume it as the safe baseline.

### Contradiction 3 — Does CloudServe lack answers, or lack a way to reach them?

**Who says what:** Sofia says documentation is "fine," the problem is that
she "cannot find things in it," and that about seven in ten tickets are ones
she's answered before. Marcus, by contrast, admits he doesn't know the
ticket breakdown at all and would be "guessing" — implicitly suggesting he
doesn't know whether the underlying problem is missing answers or a
process/search gap.

**How the data settles it:** `labels.answerable_from_docs=True` for 357 of
500 tickets (**71.4%**), closely matching Sofia's "seven out of ten"
estimate. The remaining 28.6% genuinely have no documentable answer
(feature requests, novel incidents — Ines's named categories). Separately,
Marcus's guessed dominant categories (authentication, deployment, billing,
API) do not match the actual data: the two single largest intents by volume
are `data_export` and `data_residency` (5.8% each), and the full 22-class
distribution is close to flat, with no category exceeding 5.8% of volume —
confirming his "I'd be guessing" was an accurate admission, not false
modesty.

**What follows:** Sofia is right and her diagnosis is the correct one to
design around: the majority problem is findability and confidence, not
missing documentation. This directly determines the shape of the solution
(retrieval/search quality) rather than a documentation-authoring effort.

---

## Problem statement

| Element | Statement | Supporting section/row |
|---|---|---|
| What the client asked for | A chatbot that answers easy tickets automatically so agents can focus on hard ones, to bring first-response time inside the 2-hour SLA | Interview 1 (Marcus) |
| What the evidence suggests they actually need | A system that correctly recognizes which of the 71.4% of tickets are answerable from the existing, accurate 29-article knowledge base, retrieves the right article despite customers not using the article's own wording, and hands anything else — including the 17.4% of tickets flagged as never-automate and the currently worst-served enterprise tier — to a human with enough context to resolve it in one pass | Section 2 (`answerable_from_docs`), Section 6 (Contradiction 3), Section 5 (risk register) |
| The gap between those two | Marcus is managing to a reported metric (first-response SLA) while naming a different one (FCR, 43.8% vs. a 65% benchmark) as what actually matters; the operational bottleneck his own team describes is not ticket volume but findability (Sofia), escalation handoff quality (Daniel), and search that doesn't match customer language (Ines) — none of which a faster first reply alone fixes | Section 4 (sentence test), Section 3 (Sofia's process reconstruction) |
| Who is affected and how | All customers via slow FCR and a 2.97/5 average CSAT; enterprise customers specifically, who today receive the slowest, least-resolved service of any tier despite being the tier CloudServe is most anxious not to downgrade; tier two engineers, who receive escalations with no triage context and have to re-ask customers what they already said | Section 6 (Contradiction 2), Interview 3 (Daniel) |
| What will change if this is solved | FCR rises above the 43.8% baseline without an increase in incorrect or policy-violating automated responses; discretionary escalations (54% of all escalations) shrink because tier one resolves them with better-found answers instead of forwarding them; tier two receives escalations with a confidence-flagged summary instead of a bare forward | Section 6 (Contradiction 1), Section 3 |
| What is explicitly not in scope | The 28.6% of tickets with no documentable answer (feature requests, roadmap questions, genuinely novel incidents per Ines); the 17.4% of tickets flagged `must_not_auto_respond` (security, compliance, billing commitments, data residency per Daniel); reconstructing or validating agents' private, unreviewed answer files | Section 5 (risk register), Interview 3 (Daniel), Interview 4 (Ines) |

**The one paragraph version:**

CloudServe's support team is not simply short-staffed or slow to reply — most
of the tickets it handles already have a correct answer sitting in its own
documentation, but agents can't reliably find that answer under time
pressure, so they either reconstruct it from memory, pull it from an
unreviewed personal file, or forward it to a more senior engineer who has to
start over without knowing what was already tried. That pattern eats time
on tickets that were never actually hard, drives up the first-contact
resolution shortfall leadership is judged on, and — contrary to the
assumption that CloudServe's best customers are already well served — is
currently hitting its premium enterprise customers hardest of all. Fixing
this means making the right existing answer easy to find and trust for the
tickets that have one, and handing everything else to a person with a clear
note on what's already known, while never letting the system touch the
handful of ticket types — security, compliance, billing commitments, and
anything with no answer on file — where a wrong guess does real damage.

*Read back against each interview:* Marcus would recognize this as explaining
his FCR gap in terms he didn't have data for. Sofia would recognize her own
diagnosis validated rather than overridden. Daniel would recognize the
mandatory/discretionary escalation split he was gesturing at. Ines would
recognize provenance and scope boundaries she asked for. Ravi would object
mildly: this paragraph doesn't mention honesty/labeling of automated
responses, which matters to him — recorded here as a gap the solution design
stage should not drop.
