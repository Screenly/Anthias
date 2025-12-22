import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Provider } from 'react-redux'
import { store } from './store'

import '@/sass/anthias.scss'
import { App } from '@/components/app'

const appElement = document.getElementById('app')
if (!appElement) {
  throw new Error('App element not found')
}

const root = ReactDOM.createRoot(appElement)

root.render(
  <BrowserRouter basename="/">
    <Provider store={store}>
      <App />
    </Provider>
  </BrowserRouter>,
)
