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

## 4. Paste two things into Cloud Shell

**4a. Your Gemini key, on its own.** Paste this line, press Enter, then paste
your key when it asks and press Enter again. The key does not show while you
paste; it prints only its last four characters so you can check it is the right
one.

```bash
read -rs -p "Paste your Gemini key (it will not show), then press Enter: " GEMINI_KEY; echo; echo "got a key ending in ...${GEMINI_KEY: -4}"
```

It is a separate step because the block below would otherwise swallow its own
next line as your key: a pasted block keeps typing.

**4b. Everything else, all at once.** Change the Project ID on the second line if
yours is different, paste the whole block, press Enter. About two minutes.

```bash
(
set -e
PROJECT_ID="rbac-rag-472913"
REPO="Shrey-Parikh31/rbac-rag-assistant"
# Runs inside ( ): if a step fails, this block stops and the terminal stays
# open with the error on screen, instead of closing and taking it with it.
# Every step checks before creating, so running it again is safe.
exists() { "$@" >/dev/null 2>&1; }
test -n "$GEMINI_KEY" || { echo "Run step 4a first: no Gemini key in this terminal."; exit 1; }

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# The services this uses. Everything else stays switched off.
gcloud services enable run.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

# Tokens for the deployed service. The student one is deliberately public --
# it goes in the README so anyone can try the demo, and a student can only ever
# see public material. Staff and admin are random and different from the
# development ones in the repository, which is public: a token anyone can read
# is not a clearance. If they already exist they are reused, not replaced.
if exists gcloud secrets describe kb-tokens; then
  TOKENS=$(gcloud secrets versions access latest --secret=kb-tokens)
else
  TOKENS="demo-student:student,$(openssl rand -hex 16):staff,$(openssl rand -hex 16):admin"
  printf '%s' "$TOKENS" | gcloud secrets create kb-tokens --data-file=- --replication-policy=automatic
fi

# The Gemini key, stored in Secret Manager and never printed. The demo uses it
# to look up questions it has never seen; generated answers are switched off.
if exists gcloud secrets describe gemini-key; then
  printf '%s' "$GEMINI_KEY" | gcloud secrets versions add gemini-key --data-file=-
else
  printf '%s' "$GEMINI_KEY" | gcloud secrets create gemini-key --data-file=- --replication-policy=automatic
fi

# The identity the website runs as: it may read its two secrets and nothing
# else. Its own account rather than the project's default one, which a new
# project may not even have yet and which starts with far more access.
RUNTIME="kb-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
if ! exists gcloud iam service-accounts describe "$RUNTIME"; then
  gcloud iam service-accounts create kb-runtime --display-name="rbac-rag website"
  sleep 15   # a new account takes a few seconds to be usable everywhere
fi
for SECRET in kb-tokens gemini-key; do
  gcloud secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:$RUNTIME" \
    --role=roles/secretmanager.secretAccessor --quiet > /dev/null
done

# The identity the pipeline acts as: may deploy the website and run it as the
# account above. Nothing else.
SA="gh-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
if ! exists gcloud iam service-accounts describe "$SA"; then
  gcloud iam service-accounts create gh-deployer --display-name="GitHub Actions deployer"
  sleep 15
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" \
  --role=roles/run.admin --quiet > /dev/null
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" --member="serviceAccount:$SA" \
  --role=roles/iam.serviceAccountUser --quiet > /dev/null

# Trust GitHub's word instead of storing a password.
#
# GitHub signs a statement saying "this run, from this repository". Google is
# told to accept exactly that statement and nothing else. No key is created, so
# there is no key to leak, rotate, or find in a log two years from now. The
# attribute-condition is the part that matters: without it, any repository on
# GitHub could ask for this permission.
exists gcloud iam workload-identity-pools describe github --location=global || \
  gcloud iam workload-identity-pools create github --location=global --display-name="GitHub"
exists gcloud iam workload-identity-pools providers describe github \
  --location=global --workload-identity-pool=github || \
  gcloud iam workload-identity-pools providers create-oidc github \
    --location=global --workload-identity-pool=github \
    --display-name="GitHub Actions" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='${REPO}'" \
    --issuer-uri="https://token.actions.githubusercontent.com"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO}" \
  --quiet > /dev/null

echo
echo "==================== GitHub repository VARIABLES ===================="
echo "GCP_PROJECT_ID       = ${PROJECT_ID}"
echo "GCP_SERVICE_ACCOUNT  = ${SA}"
echo "GCP_WIF_PROVIDER     = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github"
echo
echo "==================== GitHub repository SECRET ======================"
echo "KB_TOKENS_DEPLOYED   = ${TOKENS}"
echo
echo "demo-student is public on purpose. Keep the staff and admin tokens private:"
echo "anyone holding the admin one can read the confidential document."
)
```

If it stops with an error, the error stays on screen: send a screenshot of it.
Running the block again after a fix is safe, and reuses anything already made.

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
