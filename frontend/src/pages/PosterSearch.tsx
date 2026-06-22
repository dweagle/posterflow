import DriveSearchPanel from '../components/DriveSearchPanel'
import './PosterSearch.css'

function PosterSearch() {
  return (
    <div className="page-container">
      <div className="poster-search-header">
        <h1>Poster Search</h1>
        <p>Search synced Google Drive poster folders and preview matching posters.</p>
      </div>

      <DriveSearchPanel autoFocus enableSlashFocus />
    </div>
  )
}

export default PosterSearch
