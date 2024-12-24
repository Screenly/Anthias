import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { Provider } from 'react-redux'
import { store } from './store'

import '@/sass/anthias.scss'
import { App } from '@/components/app'

const root = ReactDOM.createRoot(document.getElementById('app'))

root.render(
  <BrowserRouter basename="react">
    <Provider store={store}>
      <App />
    </Provider>
  </BrowserRouter>
)
