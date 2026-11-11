import React from 'react'
import ReactDOM from 'react-dom/client'
// Tokens and the reset load before anything App pulls in, so every component sheet can override them.
import './styles/tokens.css'
import './styles/globals.css'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)