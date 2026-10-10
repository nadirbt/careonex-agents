# AWS Team Setup (local development)

CareOneX uses AWS IAM Identity Center (SSO) profiles rather than hardcoded access keys.

## Windows PowerShell

```powershell
aws configure sso --profile careonex-team
aws sso login --profile careonex-team
$env:AWS_PROFILE = "careonex-team"
$env:AWS_DEFAULT_REGION = "us-east-1"
aws sts get-caller-identity
```

Ask your team's AWS administrator for the SSO portal URL, account assignment, and necessary permissions. Do not commit the portal URL, real user identifiers, long-lived access keys, or `~/.aws` credential caches.

The live Nova 2 Sonic client needs permission to invoke its streaming model; the retrieval API needs Bedrock Knowledge Base `Retrieve` access. Direct Nova Lite model invocation is **not required** for model-free query expansion.

For Docker Compose on Windows, set `$env:HOME = $HOME` in every terminal where you run `docker compose`, and make sure AWS SSO login has succeeded on the host. The `~/.aws` folder is mounted into the container; it must never be copied into the image or repository.

**Do not run `make run` against shared cloud resources without team approval.** The pipeline can upload documents, replace indexed chunks, and start billed ingestion jobs.
