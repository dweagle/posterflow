import { useEffect, useState } from 'react'
import { Image as ImageIcon } from 'lucide-react'
import './GoogleDriveCredentialsGuide.css'

// Every PNG in src/assets/guide/ named in a step's Shots list becomes a numbered Preview button; a missing file just skips that preview
const shots = import.meta.glob('../../assets/guide/*.png', { eager: true, import: 'default' }) as Record<string, string>

type Enlarged = { src: string; caption: string }
type OpenShot = (shot: Enlarged) => void

type ShotItem = { name: string; caption: string }

// One step's previews in reading order; numbered when there is more than one, skipping any PNG that is missing
function Shots({ items, onOpen }: { items: ShotItem[]; onOpen: OpenShot }) {
  const found = items.filter((s) => shots[`../../assets/guide/${s.name}.png`])
  return (
    <>
      {found.map(({ name, caption }, i) => {
        const n = found.length > 1 ? i + 1 : null
        const src = shots[`../../assets/guide/${name}.png`]
        const enlarged = n ? `${n} of ${found.length}: ${caption}` : caption
        return (
          <button key={name} type="button" className="guide-shot-btn" onClick={() => onOpen({ src, caption: enlarged })}>
            <ImageIcon size={14} />
            {n ? `Preview ${n}: ${caption}` : `Preview: ${caption}`}
          </button>
        )
      })}
    </>
  )
}

function ProjectAndApiSteps({ prefix, onOpen }: { prefix: 'A' | 'B'; onOpen: OpenShot }) {
  return (
    <>
      <div className="instruction-step">
        <strong>{prefix}1. Create a Google Cloud project</strong>
        <ul>
          <li>Go to <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer">Google Cloud Console</a></li>
          <li>Click the <strong>project name button</strong> in the top-left header. It shows your current project name, for example "My First Project".</li>
          <li>In the dialog, click <strong>"New project"</strong> in the top-right corner. Name it "PosterFlow" and click "Create".</li>
          <li>If you already have other projects, pick the new one in that dialog before continuing.</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'a1-new-project', caption: 'The project picker and the New project dialog' },
            { name: 'a1-new-project-form', caption: 'The New Project form and its Create button' },
          ]}
        />
      </div>

      <div className="instruction-step">
        <strong>{prefix}2. Enable the Google Drive API</strong>
        <ul>
          <li>Click the <strong>&#9776; menu</strong> (top-left) → "APIs &amp; Services" → "Library"</li>
          <li>Search for "Google Drive API", open it, and click "Enable"</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'a2-open-menu', caption: 'The menu button in the top-left' },
            { name: 'a2-menu-library', caption: 'APIs & Services, then Library' },
            { name: 'a2-search', caption: 'Searching the library for Google Drive API' },
            { name: 'a2-select-drive-api', caption: 'The Google Drive API result to open' },
            { name: 'a2-enable', caption: 'The Enable button. It reads Manage once the API is on' },
          ]}
        />
      </div>
    </>
  )
}

function ServiceAccountSteps({ onOpen }: { onOpen: OpenShot }) {
  return (
    <>
      <ProjectAndApiSteps prefix="A" onOpen={onOpen} />

      <div className="instruction-step">
        <strong>A3. Create the service account</strong>
        <ul>
          <li>Click the <strong>&#9776; menu</strong> → "IAM &amp; Admin" → "Service Accounts"</li>
          <li>Click "Create service account". Name it "posterflow". Click "Create and continue".</li>
          <li>Skip the two optional permission steps. Click "Done".</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'a3-menu-service-accounts', caption: 'IAM & Admin, then Service Accounts' },
            { name: 'a3-service-accounts-page', caption: 'The Create service account button' },
            { name: 'a3-create-form', caption: 'The form with a name typed in, then Create and continue' },
            { name: 'a3-permissions', caption: 'The two optional steps. Continue, then Done' },
          ]}
        />
      </div>

      <div className="instruction-step">
        <strong>A4. Download a key file</strong>
        <ul>
          <li>Click the new service account, then open the <strong>"Keys"</strong> tab</li>
          <li>"Add key" → "Create new key" → "JSON" → "Create". A .json file downloads.</li>
          <li>Keep that file private. Anyone who has it can use the account.</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'a4-keys-tab', caption: 'The Keys tab and the Add key button' },
            { name: 'a4-add-key-menu', caption: 'Add key open, then Create new key' },
            { name: 'a4-create-key', caption: 'The Create private key dialog with JSON selected, then Create' },
          ]}
        />
      </div>

      <div className="instruction-step">
        <strong>A5. Upload it to PosterFlow</strong>
        <ul>
          <li>Click "Upload Service Account JSON" below, choose the file, then Save</li>
          <li>Nothing needs to be shared with the service account. It can read the community drives on its own.</li>
          <li>It never sees your own Google Drive unless you share a folder with its email address.</li>
          <li>If Google says key creation is disabled, your account is in a Workspace organization that blocks it. Ask its admin, or use Option B.</li>
        </ul>
      </div>
    </>
  )
}

function OAuthSteps({ onOpen }: { onOpen: OpenShot }) {
  return (
    <>
      <ProjectAndApiSteps prefix="B" onOpen={onOpen} />

      <div className="instruction-step">
        <strong>B3. Configure the OAuth consent screen</strong>
        <ul>
          <li>Click the <strong>&#9776; menu</strong> → "APIs &amp; Services" → "OAuth consent screen". Click "Get started".</li>
          <li><strong>App information:</strong> app name "PosterFlow" and your support email. Next.</li>
          <li><strong>Audience:</strong> "External". Next.</li>
          <li><strong>Contact information:</strong> your email. Next.</li>
          <li>Agree to the policy. Click "Continue", then "Create".</li>
          <li>The bold names in the steps below are pages in the left-side menu of this screen.</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'b3-menu-consent-screen', caption: 'APIs & Services, then OAuth consent screen' },
            { name: 'b3-get-started', caption: 'The Get started button' },
            { name: 'b3-app-information', caption: 'App information with a name and support email, then Next' },
            { name: 'b3-audience', caption: 'Audience set to External, then Next' },
            { name: 'b3-contact-information', caption: 'Contact information, then Next' },
            { name: 'b3-finish', caption: 'Agree to the policy. Continue, then Create' },
          ]}
        />
      </div>

      <div className="instruction-step">
        <strong>B4. Branding (required since 2026)</strong>
        <ul>
          <li>Google no longer lets you publish until this page is complete. Click <strong>"Branding"</strong>.</li>
          <li><strong>App home page</strong> and <strong>Privacy policy:</strong> links to two pages on a site you control. The site's domain must also be listed under <strong>"Authorized domains"</strong> on this page. Google rejects github.com links.</li>
          <li>No website? Make a free GitHub Pages site at <code>username.github.io</code> with a short home page and a short privacy page. The privacy page can say that PosterFlow runs on your own server, reads Google Drive only to sync posters, and shares nothing. Then add <code>username.github.io</code> under Authorized domains.</li>
          <li>Google may ask you to verify the domain in Google Search Console. Use the same Google account as this project.</li>
          <li>Click "Save".</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'b4-branding', caption: 'The Branding page with home page, privacy policy and authorized domains filled in' },
          ]}
        />
      </div>

      <div className="instruction-step">
        <strong>B5. Data Access</strong>
        <ul>
          <li>Click <strong>"Data Access"</strong> → "Add or remove scopes"</li>
          <li>Check <code>.../auth/drive</code>, "See, edit, create, and delete all of your Google Drive files"</li>
          <li>Do not pick <code>.../auth/drive.readonly</code> or any other Drive scope. Uploads need full access.</li>
          <li>Click "Update", then "Save"</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'b5-data-access', caption: 'Data Access, then Add or remove scopes' },
            { name: 'b5-scope-picker', caption: 'The scope picker with auth/drive checked, then Update' },
            { name: 'b5-save', caption: 'The scope in the table, then Save' },
          ]}
        />
      </div>

      <div className="instruction-step">
        <strong>B6. Publish the app</strong>
        <ul>
          <li>Click <strong>"Audience"</strong> → "Publish app" → confirm</li>
          <li>The button stays grey until Branding is complete. Publishing does not send the app to Google for review.</li>
          <li>⚠️ Apps left in "Testing" get tokens that <strong>expire every 7 days</strong>. You would have to redo step B8 every week.</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'b6-publish-app', caption: 'Audience, then Publish app' },
          ]}
        />
      </div>

      <div className="instruction-step">
        <strong>B7. Create the OAuth client</strong>
        <ul>
          <li>Click <strong>"Clients"</strong> → "Create client"</li>
          <li>Application type "Desktop app". Name it "PosterFlow Client". Click "Create".</li>
          <li>⚠️ <strong>Copy the Client ID and Client Secret from the popup now.</strong> Google does not show the secret again.</li>
          <li>Also click "Download JSON" and keep that file somewhere safe. It holds both values in case you need them later.</li>
        </ul>
        <Shots
          onOpen={onOpen}
          items={[
            { name: 'b7-clients-page', caption: 'Clients, then Create client' },
            { name: 'b7-desktop-app', caption: 'Application type set to Desktop app' },
            { name: 'b7-create', caption: 'The form with a name typed in, then Create' },
            { name: 'b7-client-created', caption: 'The popup with the Client ID, Client Secret and Download JSON' },
          ]}
        />
      </div>

      <div className="instruction-step">
        <strong>B8. Generate the token</strong>
        <ul>
          <li>Use a computer with a web browser, like the desktop or laptop you are reading this on. It does not need to be the machine that runs PosterFlow.</li>
          <li>The command opens the browser on the same computer and waits there for Google's answer. It does not work over SSH, inside Docker, or on a headless server or NAS. The token it prints works from any machine.</li>
          <li>Install rclone on that computer: <a href="https://rclone.org/install/" target="_blank" rel="noopener noreferrer">rclone.org/install</a>. No other rclone setup is needed.</li>
          <li>Open a terminal (PowerShell on Windows) and run:</li>
          <li><code>rclone authorize "drive" "YOUR_CLIENT_ID" "YOUR_CLIENT_SECRET"</code></li>
          <li>A browser opens. Sign in. Google warns that it has not verified this app. Click "Advanced", then "Go to PosterFlow (unsafe)". That warning is normal for a personal app, and you do not need to request verification.</li>
          <li>The terminal prints a token JSON. Copy everything from &#123; to &#125; and paste it into the token field below.</li>
          <li>Enter the Client ID and Client Secret, then Save.</li>
        </ul>
      </div>
    </>
  )
}

type GuideOption = 'a' | 'b'

type GuideProps = {
  open: boolean
  onToggle: () => void
}

export function GoogleDriveCredentialsGuide({ open, onToggle }: GuideProps) {
  const [option, setOption] = useState<GuideOption>('a')
  const [enlarged, setEnlarged] = useState<Enlarged | null>(null)

  useEffect(() => {
    if (!enlarged) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setEnlarged(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enlarged])

  return (
    <>
      <button type="button" className="instructions-toggle" onClick={onToggle}>
        {open ? '▼' : '▶'} How to set up Google Drive access
      </button>

      {open && (
        <div className="instructions-box">
          <h3>Step-by-step Guide</h3>

          <div className="instruction-step">
            <strong>Pick one option, then open its tab below</strong>
            <ul>
              <li><strong>Option A, service account:</strong> for syncing the community poster drives. Quicker to set up, and it never expires.</li>
              <li><strong>Option B, OAuth client:</strong> for poster makers who upload to their own Google Drive. It covers syncing too, so you do not need both.</li>
              <li>If both are filled in, PosterFlow uses the service account for everything, and it can only read.</li>
            </ul>
          </div>

          <div className="guide-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={option === 'a'}
              className={`guide-tab ${option === 'a' ? 'active' : ''}`}
              onClick={() => setOption('a')}
            >
              <span className="guide-tab-title">Option A: Service account</span>
              <span className="guide-tab-sub">Sync the community drives. Nothing expires.</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={option === 'b'}
              className={`guide-tab ${option === 'b' ? 'active' : ''}`}
              onClick={() => setOption('b')}
            >
              <span className="guide-tab-title">Option B: OAuth client</span>
              <span className="guide-tab-sub">Poster makers who upload to their own Google Drive.</span>
            </button>
          </div>

          <p className="guide-tab-hint">
            {option === 'a'
              ? 'Showing the service account steps. Poster makers who upload should open Option B.'
              : 'Showing the OAuth steps. Sync-only users can use the shorter Option A.'}
          </p>

          {option === 'a' ? <ServiceAccountSteps onOpen={setEnlarged} /> : <OAuthSteps onOpen={setEnlarged} />}
        </div>
      )}

      {enlarged && (
        <div className="guide-lightbox" role="dialog" aria-label={enlarged.caption} onClick={() => setEnlarged(null)}>
          <img src={enlarged.src} alt={enlarged.caption} />
          <p>{enlarged.caption}</p>
        </div>
      )}
    </>
  )
}

type ConflictProps = {
  serviceAccountFile: string
  clientId: string
  clientSecret: string
  token: string
}

export function GoogleCredentialsConflictNotice({ serviceAccountFile, clientId, clientSecret, token }: ConflictProps) {
  const hasServiceAccount = serviceAccountFile.trim() !== ''
  const hasOAuth = [clientId, clientSecret, token].some((value) => value.trim() !== '')
  if (!hasServiceAccount || !hasOAuth) return null
  return (
    <div className="credentials-conflict" role="alert">
      Both a service account and OAuth fields are set. PosterFlow will use the service account for everything, and it can only read. Poster makers who upload should clear the service account path.
    </div>
  )
}
