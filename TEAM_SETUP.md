# CareOneX — Team AWS Setup Playbook

Purpose: let every teammate run the CareOneX voice agent locally against the **shared team AWS account** (with shared Bedrock credits), instead of each person using a personal account.

This is written so it can be executed live in a meeting. It has three parts:

- **Part A — Account owner (Nadir / root):** one-time account setup.
- **Part B — Per teammate:** what the owner creates for each of us.
- **Part C — Each teammate's laptop:** run the voice agent locally.

The app only needs permission to call one model, **Amazon Nova 2 Sonic**, in **us-east-1**. Nothing here touches the existing frontend/backend.

---

## Key concepts (1 minute)

- **One team account** holds the resources and the **shared credits**. Teammates do **not** need personal AWS accounts.
- Each teammate gets an **identity** in that account plus a **permission set / policy**. Two supported options:
  - **IAM Identity Center (SSO)** — recommended. You log in (browser), AWS mints **temporary** keys that expire. Safer, no long-lived secrets.
  - **IAM user** — simpler. Long-lived access key + secret per person.
- Local experiments work with **either** option because they both end up as standard AWS credentials on your laptop.

> Root user is for account setup only. After Part A, the owner should operate as an admin identity, not root.

---

## Part A — Account owner (do once)

Pick **A1 (SSO, recommended)** or **A2 (IAM users, simpler)**.

### Least-privilege policy for the voice agent

Create a customer-managed policy named **`CareOneXNovaSonic`** with this JSON. The app uses the bidirectional streaming action; `InvokeModel` is included for quick tests.

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

Also enable **model access** once for the account: Bedrock console → **Model access** → enable **Amazon Nova 2 Sonic** in **us-east-1**.

### A1 — IAM Identity Center (SSO), recommended

1. Enable **IAM Identity Center** (same region hub is fine; the model call is still us-east-1).
2. **Permission set** → create `CareOneXVoice`. Attach the **`CareOneXNovaSonic`** policy (as a customer-managed or inline policy). Set session duration (e.g. 8 hours).
3. **Users** → create one per teammate using their **work email** (this is just a login identifier).
4. **AWS accounts** → select the team account → **Assign users** → pick the teammate → attach the **`CareOneXVoice`** permission set.
5. Give teammates the **Start URL** (looks like `https://d-xxxx.awsapps.com/start`) and the **SSO region**.

Each teammate then gets an email invite to set password + MFA.

### A2 — IAM users (simpler fallback)

1. **IAM → User groups** → create `careonex-voice`. Attach **`CareOneXNovaSonic`**.
2. **IAM → Users** → create one user per teammate (e.g. `conny`), **no console access needed**, add to group `careonex-voice`.
3. For each user: **Security credentials → Access keys → Create access key → CLI**.
4. Send each teammate **their** access key + secret **securely** (password manager / one-time link) — never in chat or git.

---

## Part B — What each teammate receives

From **A1 (SSO)**: an **email invite**, the **Start URL**, and the **SSO region**.

From **A2 (IAM user)**: their **access key id** + **secret access key** (securely), and the region `us-east-1`.

Everyone shares the **same account and credits**; billing is centralized on the owner's account.

---

## Part C — Each teammate's laptop

Prereqs: macOS with Homebrew, Python 3.11/3.12, this repo cloned.

```bash
brew install portaudio awscli
cd CareOneX
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### C1 — If you were set up via SSO (A1)

> SSO resolution in the app uses **botocore**, which is installed automatically with `awscli` (Part C prereqs). If you skipped `awscli`, `pip install botocore` into the venv.

```bash
aws configure sso
# SSO start URL:   <paste Start URL>
# SSO region:      <paste SSO region>
# choose the team account and the CareOneXVoice role
# CLI default region: us-east-1
# CLI output format:  json
# name the profile:   careonex-team
```

Each working session:

```bash
aws sso login --profile careonex-team
export AWS_PROFILE=careonex-team
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity        # should show the TEAM account id
python -m nova_sonic
```

SSO keys are temporary; re-run `aws sso login` when they expire. The app resolves SSO profiles automatically (botocore is installed with `awscli`).

### C2 — If you were set up as an IAM user (A2)

```bash
aws configure --profile careonex-team
# AWS Access Key ID:     <your key>
# AWS Secret Access Key: <your secret>
# Default region:        us-east-1
# Default output:        json
```

Each working session:

```bash
export AWS_PROFILE=careonex-team
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity        # should show the TEAM account id
python -m nova_sonic
```

### Verify it's the team account, not your personal one

`aws sts get-caller-identity` must show the **team account id**. If it shows your personal account, run:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
export AWS_PROFILE=careonex-team
```

`InvalidClientTokenId` or `ExpiredToken` → re-auth (`aws sso login` for SSO) or re-check keys. The app's "Listening" line alone does **not** prove authentication.

---

## Meeting agenda (suggested, ~20 min)

1. Owner confirms **which team account** holds the credits (2 min).
2. Owner enables **Bedrock model access** for Nova 2 Sonic in us-east-1 (2 min).
3. Owner creates **`CareOneXNovaSonic`** policy (3 min).
4. Choose **SSO (A1)** or **IAM users (A2)** and create identities for each teammate (8 min).
5. One teammate does **Part C** live end-to-end as a smoke test (5 min).

---

## Cost / credits

- The shared credits sit in the **team account**; usage is billed centrally.
- Nova 2 Sonic is billed per streamed audio/token usage. Keep test sessions short (the stream also caps around ~8 minutes).
- Owner can watch **Billing → Cost Explorer**, filter to **Amazon Bedrock**, daily **Unblended cost**; credits offset but usage is still metered.

---

## Message to send Nadir (copy/paste)

> For today: can we set up team AWS access for the CareOneX voice agent? It only needs Amazon **Nova 2 Sonic** in **us-east-1**.
>
> Proposed: enable Bedrock model access for Nova 2 Sonic; create a policy `CareOneXNovaSonic` allowing `bedrock:InvokeModelWithBidirectionalStream` + `bedrock:InvokeModel` on that model; then give each of us access via **IAM Identity Center** (a `CareOneXVoice` permission set) — or IAM users if that's faster. Send us the SSO **Start URL + region** (or our IAM keys securely). We'll run it locally with `AWS_PROFILE=careonex-team`. Full steps are in `TEAM_SETUP.md`.
