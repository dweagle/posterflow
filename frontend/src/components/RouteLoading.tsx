import './RouteLoading.css'

// Shown while a page's chunk is fetched the first time it opens in a session.
export default function RouteLoading() {
  return <div className="route-loading" role="status">Loading…</div>
}
