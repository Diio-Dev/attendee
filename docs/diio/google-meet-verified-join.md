# Google Meet: join as a verified participant, not an anonymous bot

Proposal for the diio fork of Attendee (`Diio-Dev/attendee`). Written 2026-11-04.

## The problem

Since Google's March/April 2026 change, Meet sorts every join request into two queues.
Invitees and members of the host's organization land in the normal queue. Everyone else lands
in a queue the host sees labelled as a potential risk, where the default button is **Deny**.
Hosts have to open the three-dot menu to admit. Vendors and customers describe this as
"Meet says the notetaker is a bot" (Nylas forum, Apr/May 2026; Airspeed FAQ; UC Today,
2026-03-25).

Google does not publish the classifier. What the public evidence says:

| Source | Claim |
|---|---|
| Google Workspace Learning Center | "Anonymous users or third-party bots, like note takers" are the group that gets auto-denied when knocking is off. With Trusted access, members of the host org and **external Google accounts invited through the Calendar event join without knocking**. |
| Meeting BaaS (blog, 2026) | Anonymous, data-center bots land in the risk queue; a bot signed in as a real Workspace user through SAML lands in the verified queue. Residential proxies do not help. Caps logins at 20 concurrent sessions. |
| Recall.ai docs | Signed-in alone is not sufficient; signed-in **and invited** skips knocking. The risk classification is "separate" and a signed-in bot "may still be flagged"; contact support for that. |
| Attendee maintainer (issue #841, 2026-05-29) | The hard block ("You can't join this video call") is IP-based; on Kubernetes the pod is recreated to get a new IP. Recommends signed-in bots. |
| Attendee code | `ui_interaction_mode: humanized` exists "to evade bot detection" (openapi.yml) and the serializer switches it off for always-signed-in bots because "humanized motion is not needed" in that case. |

So: identity (a signed-in Workspace user that is on the invite) is the path Google itself
documents for skipping the queue. Everything else is fingerprint hygiene with an unmeasured
effect. The maintainer's paid offer, as far as the hosted docs show, is "contact support to
enable Bot Login" plus hosting the accounts. The mechanism (SAML IdP, login groups, fallback
to login when blocked) has been in the open-source tree since PR #481 (2025-10-30) and PR #739
(2026-03-17). There is nothing to buy.

## Where diio is today

- `attendee.diio.com` runs upstream v1.89.2 on EKS, one pod per bot, bot nodes in public
  subnets with public IPs (`infrastructure/modules/attendee`). Prod launched 2026-11-02
  (diio commit `d17edbf53`).
- diio sends `google_meet_settings: { use_login: true, login_mode: 'only_if_required' }`
  (`app/adapters/attendee_options_adapter.rb`). With no Google Meet login group in the
  Attendee project, `google_meet_bot_login_is_available` is false and every bot joins
  anonymously. The 2024 `Diio-Dev/bot-idp` repo is an empty initial commit; the IdP was never
  built because Attendee now ships one.
- Recall bots on Meet are anonymous too (`recall_options_adapter.rb` has no Google login).

### What it costs us, measured

`meetings.error_cause = 10` (`bot_not_accepted`) for Recall bots on Meet, three-day windows
(production replica, 2026-11-04):

| Window | Meet bots | not_accepted | % |
|---|---|---|---|
| 2026-01-13 | 9,287 | 3,539 | 38.1 |
| 2026-02-10 | 10,470 | 4,065 | 38.8 |
| 2026-03-10 | 11,913 | 4,669 | 39.2 |
| 2026-03-31 | 8,875 | 4,102 | 46.2 |
| 2026-04-21 | 12,194 | 4,745 | 38.9 |
| 2026-06-09 | 12,596 | 5,013 | 39.8 |
| 2026-08-11 | 11,881 | 5,038 | 42.4 |
| 2026-10-13 | 14,573 | 6,151 | 42.2 |
| 2026-11-01 | 11,626 | 5,031 | 43.3 |

The baseline before Google's change was already 38 to 39 percent: no-shows and lobbies nobody
watches. The change adds roughly 3 to 4 points on top, about 150 meetings a day at current
volume. The larger cost is the label itself in front of customers, which this data cannot see.

```sql
WITH b AS (SELECT id, company_id FROM bot_services
           WHERE type = 'Bot::Recall' AND created_at >= :day AND created_at < :day::date + 3)
SELECT count(*) AS total,
       count(*) FILTER (WHERE m.error_cause = 10) AS not_accepted
FROM b JOIN meetings m ON m.bot_service_id = b.id AND m.company_id = b.company_id
WHERE m.meeting_url LIKE '%meet.google%';
```

## Proposal

### Layer 1: identity (do first, no fork code needed)

1. **Workspace for the bots.** A separate Workspace on `bots.diio.com` (subdomain of our
   domain), paid plan. Not the main diio org: the SSO profile assignment is what makes the
   trick work and we do not want it near employee logins. Recall and Attendee both document the
   same requirement.
2. **Bot users.** `notetaker-01..NN@bots.diio.com`, non-admin, display name `diio` (Google
   shows the account's name and ignores `bot_name`), avatar set to the logo. Sign in to each
   once to finish the welcome flow and set language to English (United States), otherwise the
   SAML flow fails. Size: Attendee recommends peak concurrent Meet bots / 20; with the
   300 to 400-bot peak in `prod/attendee/main.tf`, start with 20 users.
3. **Google Group** `notetaker@bots.diio.com` containing all bot users. Inviting the group
   makes whichever account the round-robin picks an invitee (Recall and Meeting BaaS both use
   this).
4. **Legacy SSO profile** in the bots Workspace admin console (Security, SSO with third-party
   IdP, "Legacy SSO profile"): sign-in URL `https://attendee.diio.com/bot_sso/google_meet_sign_in`,
   sign-out URL `https://attendee.diio.com/bot_sso/google_meet_sign_out`, domain-specific
   issuer on, certificate from `openssl req -x509 -newkey rsa:2048 -keyout key.pem -out
   cert.pem -sha256 -days 3650 -nodes`. Assign the profile to the OU holding the bot users.
   Steps are in `docs/signed_in_bots.md`.
5. **Attendee dashboard**, Settings, Bot Logins, Google Meet: one group named `diio`, one
   login per bot user with the email, `key.pem` and `cert.pem`. Credentials are encrypted at
   rest with the `CREDENTIALS_ENCRYPTION_KEY` already in SSM.
6. **Pod env** (`modules/attendee/k8s/base.yaml`): `USE_SAFE_NAVIGATION_FOR_SIGNED_IN_GOOGLE_MEET_BOTS=true`
   (we set `SITE_DOMAIN`, so the safer redirect check works). Optional:
   `ENFORCE_DOMAIN_ALLOWLIST_IN_CHROME=true` to pin the bot's Chrome to the signed
   `navigation_configs/google_meet.json` domain list.
7. **diio, `AttendeeOptionsAdapter#platform_settings`**:
   `{ use_login: true, login_mode: 'always', login_group_name: 'diio', ui_interaction_mode: 'humanized' }`.
   `humanized` has to be explicit: Attendee defaults always-signed-in bots to `robotic`.
8. **diio, invite the group on the event.** `Events::Google` already holds a `calendar.events`
   token, which can write. When the diio user is the organizer (`organizer.self?`), patch the
   event with the group added to `attendees` and `send_updates: 'none'` at the time the bot is
   scheduled (`MeetingWorker` / `bot_scheduled_at`). Customer-organized meetings and
   Microsoft-calendar companies keep the signed-in-only path. This is a product decision:
   we would be writing to our users' calendars and guests will see `diio` on the guest list.
   It is also the only path Google documents as "joins without knocking".

Expected outcome: invited bots join without a queue at all. Signed-in but uninvited bots
still knock; whether they are flagged is the open question the two vendors disagree on, and
Layer 3 answers it for us in an afternoon.

### Layer 2: automation hygiene in the fork

Applies to the anonymous fallback and as belt-and-braces for signed-in bots.

- **In this branch:** the Dockerfile strips chromedriver's `window.cdc_*` marker block at
  image build (undetected-chromedriver's technique). Verified against the pinned
  134.0.6998.88 binary: seven markers become none, size unchanged, and the step fails the
  build if a chromedriver bump changes the block.
- **Worth testing, not implemented:**
  - `--use-fake-device-for-media-stream` exposes devices named "Fake Default Audio Input" /
    "fake_device_0" to `enumerateDevices`, which Meet reads. Attendee already runs PulseAudio
    (`entrypoint.sh`, `auto_null`); a `module-null-sink` plus `module-remap-source` with
    ordinary names would let us drop the flag. The voice-agent output path uses the same
    device, so it needs a check.
  - `--disable-gpu` makes WebGL report SwiftShader, which no laptop does.
  - Chrome is pinned at 134 since March (upstream pinned it because newer Chrome broke the
    payload, PR #847). An eight-month-old major version is itself a signal.
  - Selenium's CDP session (`Runtime.enable`) is detectable by Google-grade anti-abuse. The
    only fix is a CDP-less driver, which is a rewrite of `web_bot_adapter.py`. Not now.
- **Not worth cherry-picking:** PR #895 (temporary profile instead of `--guest`) targets
  cookie bleed between bots on one Celery worker; our pods are one per bot. PR #894 (cache
  the Chrome profile in S3, join time 1:40 to 0:30) is interesting once Layer 1 is live.
- Upstream has nothing further for Meet in the open tree: issue #1058 shows the maintainer's
  Teams approach is a client flag (`isBot=true`), and the Meet work since March is the
  humanized mouse layer (PRs #847, #899).

### Layer 3: measure before we argue

1. **Controlled test (about an hour once the Workspace exists).** Host a Meet from a
   diio-owned account with host management on. From `attendee.diio.dev` send five bots per
   variant: anonymous; anonymous on the patched image; signed-in; signed-in and the group
   invited. Record which queue the host UI shows and whether the bot was admitted without a
   click. This is the only way to settle the Recall-vs-Meeting-BaaS disagreement for our
   setup.
2. **Production metric.** The query above per week and per provider, plus Attendee's
   `request_to_join_denied` and `blocked_by_platform` sub-codes from the dashboard. Success:
   Attendee's Meet `not_accepted` at or below Recall's pre-March 38 percent, and no risk label
   in the controlled test.

## Costs and risks

- Workspace: about 20 users on Business Starter, in the order of USD 150 a month against the
  USD 14k a month Recall bill the Attendee pilot exists to replace. Verify current pricing.
- The display name is the account's, so one group gives one name. If we want localized names
  per company language, that is one login group per language.
- Google's concurrency limit is per account (about 20 to 30 sessions). Round-robin spreads
  load; alarms should watch `bot_login_attempt_failed`.
- Signed-in joins take longer (upstream measures 1:40 before profile caching). Scheduled bots
  already launch 5 to 10 minutes early; unscheduled "join now" bots will feel it.
- Upstream open issues for signed-in Meet bots: #650 (Gemini modal), #649 (same login already
  in the meeting). PR #927 handles two of our bots landing in one meeting.
- Compliance: the bot keeps its name, logo and the disclaimer chat message. A Workspace
  service account named `diio` is what Google describes for a verified third-party
  participant; nothing here hides that a recorder is in the call.

## Update 2026-11-04: identity is not the signal

With the Workspace, SSO profile and login group in place, the signed-in bot still landed in the
"With potential risks" queue with a robot badge next to its avatar. Recall's bots in the same
meetings are anonymous and get no badge. So Google is reading something in the Attendee bot's
browser or network, not its account.

The strongest Attendee-specific suspect is page-level tampering by the injected payload:
`window.RTCPeerConnection` and `window.fetch` are replaced with plain functions (non-native
`toString`, foreign `prototype`, `instanceof` fails), `getUserMedia` and `getDisplayMedia` are
shadowed as own properties on `navigator.mediaDevices`, `RTCRtpReceiver.prototype` is patched,
and a dozen helpers are exposed on `window`. Recall's published Meet bot touches none of these.

### Join-only diagnostic mode

`JOIN_ONLY_DIAGNOSTIC_MODE=true` on the bot pods makes the bot join with none of the above: no
interceptors, no `window` globals. It still knocks, so the host can see which queue it lands
in. It does not record, cannot see the roster, and leaves on the only-participant or silence
timeout; read nothing into its final status.

On staging the bot pods take their env from the `env` ConfigMap, so no manifest change is
needed:

```bash
kubectl -n attendee patch configmap env --type merge -p '{"data":{"JOIN_ONLY_DIAGNOSTIC_MODE":"true"}}'
# send bots, read the queue, then
kubectl -n attendee patch configmap env --type json -p '[{"op":"remove","path":"/data/JOIN_ONLY_DIAGNOSTIC_MODE"}]'
```

Building this branch into staging needs the *Deploy Attendee* workflow's `repository` and `ref`
inputs (Diio-Dev/infrastructure): repository `Diio-Dev/attendee`, ref
`diio/google-meet-verified-join`, version `v1.89.2-diio.1`.

Decision tree: badge gone in diagnostic mode means the fix is rewriting the interceptors as
Proxies over the native constructors plus a `Function.prototype.toString` shim. Badge still
there means the next variants, one per run: fake media devices off, Chrome bumped from 134,
mouse mode `robotic`.
