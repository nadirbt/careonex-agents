# CareOneX — Team AWS Setup (as built)

Purpose: every teammate runs the CareOneX voice agent locally against the **shared team AWS account** (shared Bedrock credits), with no personal AWS account and no long-lived keys.

**Status (2026-09-29): the account side is done.** Identity Center is enabled, the `AC215` permission set and group exist, all four teammates are in the group, and the model is active. What remains is each teammate's laptop (Part C).

The app only needs permission to call one model, **Amazon Nova 2 Sonic**, in **us-east-1**. Nothing here touches the CareOneX phone line, API, database or recordings; the permission set cannot reach them.

---

## What was set up

| Item | Value |
| --- | --- |
| Team account id | `117949645823` |
| Identity Center | Enabled **with AWS Organizations**, primary region `us-east-1` |
| Access portal URL | `https://d-906661b099.awsapps.com/start` |
| Permission set | `AC215` (8-hour session, one inline policy, below) |
| Group | `AC215`, assigned to the team account with the `AC215` permission set |
| Members | See the table below (username = e-mail) |
| MFA | Required on every sign-in; authenticator app and security key/Touch ID both allowed; device enrolment forced at first sign-in |
| Model | `amazon.nova-2-sonic-v1:0` is **active and authorized** in us-east-1 (no model-access toggle left to flip) |
| Budget | `AC215-Bedrock-Monthly`: **$30/month** on Amazon Bedrock, alerts to the owner at 50%, 80%, 100% actual and 100% forecast (created 2026-09-29). The account also has a $200/month all-services budget. |

### Members of `AC215`

Each user's Identity Center username is their e-mail address. This is the address they sign in with at the portal and the one the password-reset e-mail went to.

| Name | E-mail / username |
| --- | --- |
| Caroline Li | `zhl671@g.harvard.edu` |
| Helen Jin | `helenjin@g.harvard.edu` |
| Junyi Zhou | `junyizhou@hsph.harvard.edu` |
| Marco Ren | `mren@g.harvard.edu` |

### The `AC215` inline policy

This is the entire set of permissions a teammate has. Two Bedrock actions on one model ARN in one region. No billing, no console resources, no other models.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "NovaSonicInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModelWithBidirectionalStream",
        "bedrock:InvokeModel"
      ],
      "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-sonic-v1:0"
    }
  ]
}
```

### Decisions made, and why

- **Identity Center (SSO) only. No IAM users.** Temporary keys, MFA, one place to revoke. Long-lived keys on laptops are what the account's audit baseline exists to avoid.
- **Identities live in the production account**, not a separate member account. Acceptable for a course team calling one model with synthetic audio, because the policy is a data-path dead end. The mitigations that make it a good choice: MFA Required (done), the $30/month Bedrock budget (done, see Cost), and **never widening `AC215` in place**. Move to a member account under the organization if the team grows, the agent starts handling real calls, or someone else needs admin rights.
- **Root is for setup only.** The owner should operate through an admin permission set from now on, never root.

### What teammates can see and do

- **Can see:** one entry in the access portal (the account's name and id) with one role, `AC215`. From a terminal, `aws sts get-caller-identity` shows the account id and their own role name.
- **Can do:** call Nova 2 Sonic in us-east-1. Every other Bedrock model is refused.

---

## Part A — Owner: adding, removing and changing access

Everything here is in **IAM Identity Center** in the console, region `us-east-1`.

### Add a teammate (about one minute)

1. **Users → Add user.** Username = their e-mail. Fill first name, last name, e-mail. Leave **Send an email invitation** on.
2. Under **Add user to groups**, tick **`AC215`**. Add user.
3. Send them the four lines from **Part B** yourself. The AWS invite e-mail does not include them.

> If a user was created from the command line instead of the console form, they get **no** invitation e-mail. Open **Users → the person → Reset password → "Send an email to the user with instructions for resetting the password"**. The e-mail verification link alone is not enough: it proves the address but does not set a password. The reset-password flow sets the password and forces MFA enrolment in the same pass. This was done for all four current members on 2026-09-29.

### Remove a teammate

**Groups → AC215 → Remove user**, or **Users → the person → Disable**. Their temporary keys die within the 8-hour session length.

### Change what the permission set allows

**Permission sets → AC215 → Inline policy → Edit**, save, then click **Reprovision** in the yellow banner. Everyone in the group gets the new policy at their next sign-in. Prefer not to widen it; if the team needs more, that is the signal to create a member account instead.

---

## Part B — What each teammate receives

Two things: the AWS password-reset (or invitation) e-mail, and this message from the owner:

```
Portal:  https://d-906661b099.awsapps.com/start
Region:  us-east-1
Role:    AC215
Profile: careonex-team   (aws configure sso, then aws sso login --profile careonex-team)
```

Open the AWS e-mail the same day; the link expires. If it has, use **Forgot password** at the portal URL, which sends a fresh one. The first sign-in sets your password and enrols an MFA device (authenticator app or Touch ID) in one flow.

---

## Part C — Each teammate's laptop

Prereqs: macOS with Homebrew, Python 3.11/3.12, this repo cloned.

```bash
brew install portaudio awscli
cd careonex-agents
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

> SSO resolution in the app uses **botocore**, which is installed automatically with `awscli`. If you skipped `awscli`, run `pip install botocore` inside the venv.

### C1 — Configure the SSO profile (once)

```bash
aws configure sso
# SSO session name:  careonex
# SSO start URL:     https://d-906661b099.awsapps.com/start
# SSO region:        us-east-1
# SSO registration scopes: (accept default)
# -> browser opens; sign in with your Harvard e-mail + MFA
# account:           117949645823 (the only one offered)
# role:              AC215 (the only one offered)
# CLI default region: us-east-1
# CLI output format:  json
# Profile name:       careonex-team
```

### C2 — Each working session

```bash
aws sso login --profile careonex-team
export AWS_PROFILE=careonex-team
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity
python -m nova_sonic
```

`get-caller-identity` must print account **`117949645823`** and an ARN whose role name contains **`AC215`**. Then the voice agent starts; speak after the "Listening" line.

SSO keys are temporary (8 hours). When they expire, re-run `aws sso login --profile careonex-team`.

### Troubleshooting

- **The app exits with "Set AWS credentials first"** even though `get-caller-identity` works. The app's preflight looks for `~/.aws/credentials`, which `aws configure sso` does not create. Run `touch ~/.aws/credentials` once and start the app again; the SSO profile is resolved through botocore from `~/.aws/config`.
- **It shows a different account id.** Stale personal keys in the environment win over the profile. Clear them:

  ```bash
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
  export AWS_PROFILE=careonex-team
  ```

- **`ExpiredToken` / `InvalidClientTokenId`.** Re-run `aws sso login --profile careonex-team`.
- **`AccessDeniedException` from Bedrock.** Check the model id is `amazon.nova-2-sonic-v1:0` and the region is `us-east-1`. Any other model or region is refused by design.
- The app's "Listening" line alone does **not** prove authentication; the first real error arrives when audio is sent.

---

## Cost / credits

- Credits sit in the team account and are billed centrally. Teammates cannot see credits or spend.
- **Budget: `AC215-Bedrock-Monthly`, $30/month, filtered to the Amazon Bedrock service.** E-mails the owner at 50%, 80% and 100% of actual spend and when the month's forecast passes 100%. An AWS Budget alerts; it does not stop calls by itself.

### What a call actually costs

Nova 2 Sonic converts audio to speech tokens at 25 tokens per second. Speech input is $3 per million tokens, speech output $12 per million. Text tokens (system prompt, transcripts) are negligible. This app streams the microphone continuously, so **input is billed for the whole session, silence included**; output is billed only while the agent speaks.

| Scenario | Approximate cost |
| --- | --- |
| One 5-minute test session | $0.06 to $0.08 |
| One hour of continuous conversation | $0.70 to $0.80 |
| A forgotten stream (ends at the ~8-minute cap) | under $0.20 |
| 4 teammates, 1 hour each per weekday for a month | about $60 |
| Typical course pace, 2 to 3 hours per person per week | $30 to $40 per month |

So the $30 budget covers normal course use; the alerts are there to catch a pattern change, not a single accident. Close the session (press Enter) when done anyway.
