import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import '@/sass/anthias.scss'
import { App } from '@/components/app'

const root = ReactDOM.createRoot(document.getElementById('app'))

root.render(
  <BrowserRouter basename="react">
    <App />
  </BrowserRouter>
)
