# Putting this on the internet for about nothing

One-time setup. After this, every push to `main` that passes the pipeline
deploys itself, and you never touch Google Cloud again.

**What it costs.** Cloud Run only runs the container while somebody is actually
asking it a question, and sleeps at zero the rest of the time. The free
allowance is **2 million requests a month, permanently** — not a trial — in the
`us-central1` region, which is what this setup uses. A portfolio service gets
a few hundred. The realistic bill is **$0.00**.

Step 6 sets a budget alert anyway, because "realistic" is not "guaranteed".

**The one way it could cost more.** The student token is public so anyone can
try the demo, which means anyone can also flood it on purpose. The service is
capped at one running copy, which bounds how much can be billed at once, but
someone flooding it for days could still pass $5 before you notice. The budget
alert emails you; it does not stop anything. The off switch is one command:
`gcloud run services delete kb --region us-central1`.

---

## 1. Make the image public

Cloud Run pulls the container from GitHub, so it has to be able to see it.

1. Go to <https://github.com/Shrey-Parikh31?tab=packages>
2. Click **rbac-rag-assistant**
3. Right sidebar → **Package settings**
4. Bottom of the page → **Change visibility** → **Public** → confirm

The image contains only the four policy documents, which are fictional and
already in the public repository. Nothing private is in it.

---

## 2. Create the Google Cloud account

1. <https://console.cloud.google.com> → sign in with your Google account
2. It will ask for a **card**. This is required even though you stay in the free
   tier. Google does not charge it unless you explicitly click "Activate full
   account" — a free trial that runs out **stops** rather than bills you.
3. When it offers the **$300 free trial**, take it. It is 90 days of credit you
   will not come close to using, and it changes nothing about the permanent free
   tier underneath it.
4. At the top, **create a new project** named `rbac-rag`. Note the **Project ID**
   it generates — it is usually `rbac-rag-######`, not just `rbac-rag`.

---

## 3. Open Cloud Shell

No installing anything. Top right of the console, the **`>_`** icon. A terminal
opens in the browser with everything already installed.

---

## 4. Paste this, all at once

Change the first line if your project ID is different, then paste the whole
block into Cloud Shell and press enter. It takes about two minutes and prints
what you need at the end.

```bash
PROJECT_ID="rbac-rag"          # <-- the Project ID from step 2
REPO="Shrey-Parikh31/rbac-rag-assistant"

set -e
gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# The three services this uses. Everything else stays switched off.
gcloud services enable run.googleapis.com secretmanager.googleapis.com \
  iamcredentials.googleapis.com

# Tokens for the deployed service. The student one is deliberately public --
# it goes in the README so anyone can try the demo, and a student can only ever
# see public material. Staff and admin are random and different from the
# development ones in the repository, which is public: a token anyone can read
# is not a clearance.
STUDENT="demo-student"
STAFF=$(openssl rand -hex 16)
ADMIN=$(openssl rand -hex 16)
printf '%s:student,%s:staff,%s:admin' "$STUDENT" "$STAFF" "$ADMIN" \
  | gcloud secrets create kb-tokens --data-file=- --replication-policy=automatic

# Your Gemini key, so the demo can look up questions it has never seen before.
# Typed in hidden, stored in Secret Manager, never printed. The service uses it
# for search only; generated answers are switched off on the demo.
read -rs -p "Paste your Gemini API key and press Enter (it will not show): " GEMINI_KEY; echo
printf '%s' "$GEMINI_KEY" \
  | gcloud secrets create gemini-key --data-file=- --replication-policy=automatic
unset GEMINI_KEY

# The service reads both secrets at startup.
for SECRET in kb-tokens gemini-key; do
  gcloud secrets add-iam-policy-binding "$SECRET" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor --quiet > /dev/null
done

# The identity the pipeline acts as. Deploy permissions, nothing else.
gcloud iam service-accounts create gh-deployer --display-name="GitHub Actions deployer"
SA="gh-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
for ROLE in roles/run.admin roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA" --role="$ROLE" --quiet > /dev/null
done

# Trust GitHub's word instead of storing a password.
#
# GitHub signs a statement saying "this run, from this repository". Google is
# told to accept exactly that statement and nothing else. No key is created, so
# there is no key to leak, rotate, or find in a log two years from now. The
# attribute-condition is the part that matters: without it, any repository on
# GitHub could ask for this permission.
gcloud iam workload-identity-pools create github --location=global \
  --display-name="GitHub"
gcloud iam workload-identity-pools providers create-oidc github \
  --location=global --workload-identity-pool=github \
  --display-name="GitHub Actions" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO}" \
  --quiet

echo
echo "==================== GitHub repository VARIABLES ===================="
echo "GCP_PROJECT_ID       = ${PROJECT_ID}"
echo "GCP_SERVICE_ACCOUNT  = ${SA}"
echo "GCP_WIF_PROVIDER     = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github"
echo
echo "==================== GitHub repository SECRET ======================"
echo "KB_TOKENS_DEPLOYED   = ${STUDENT}:student,${STAFF}:staff,${ADMIN}:admin"
echo
echo "The tokens above are shown once here and stored in Secret Manager."
echo "demo-student is public on purpose. Keep the staff and admin ones private:"
echo "anyone holding the admin one can read the confidential document."
```

**Already ran an earlier version of this block?** It made a random student token
and no Gemini secret. Run these two lines instead of starting over; the second
asks for your key:

```bash
gcloud secrets versions access latest --secret=kb-tokens | sed 's/^[^:]*:student/demo-student:student/' | gcloud secrets versions add kb-tokens --data-file=-
read -rs -p "Gemini API key: " K; echo; printf '%s' "$K" | gcloud secrets create gemini-key --data-file=-; unset K; gcloud secrets add-iam-policy-binding gemini-key --member="serviceAccount:$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor --quiet
```

Then update `KB_TOKENS_DEPLOYED` in GitHub so its student token reads `demo-student`.

---

## 5. Put those four values into GitHub

<https://github.com/Shrey-Parikh31/rbac-rag-assistant/settings/secrets/actions>

**Variables** tab → *New repository variable*, three times:

| Name | Value |
|---|---|
| `GCP_PROJECT_ID` | from the output |
| `GCP_SERVICE_ACCOUNT` | from the output |
| `GCP_WIF_PROVIDER` | from the output |

**Secrets** tab → *New repository secret*, once:

| Name | Value |
|---|---|
| `KB_TOKENS_DEPLOYED` | the long `...:student,...:staff,...:admin` line |

Variables are visible in logs; secrets are not. The tokens are the only thing
here that is actually a credential, which is why only it is a secret.

The `deploy` job is skipped until `GCP_PROJECT_ID` exists, so nothing changes
until you finish this.

---

## 6. The money alarm

<https://console.cloud.google.com/billing> → **Budgets & alerts** → *Create budget*

- Amount: **$1**
- Alert at **100%** of actual spend
- Email yourself

A budget alert **emails, it does not cap**. It is a smoke detector, not a
sprinkler. At $1 you will hear about anything unexpected long before it matters.

**If you ever want it gone entirely:** Cloud Shell, `gcloud run services delete kb
--region us-central1`. Deleting the project removes everything.

---

## What happens after this

Push to `main` → the pipeline runs its gates → if all pass:

1. A new revision deploys to Cloud Run **with no traffic**, on its own private
   URL.
2. The full end-to-end suite runs against that revision — on the internet, in
   the region, with the real secrets.
3. Only if it passes does traffic move to it.

A broken revision is never in front of a user, so there is nothing to roll back
from. That is deliberately stronger than the "deploy, then roll back" the
roadmap asks for: a rollback means somebody already got the bad version.

Your URL will be printed by the **promote** step, and looks like
`https://kb-<hash>-uc.a.run.app`.
