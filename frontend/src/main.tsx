import '@radix-ui/themes/styles.css'
import 'katex/dist/katex.min.css'
import 'react-image-crop/dist/ReactCrop.css'
import './styles.css'

import { Theme } from '@radix-ui/themes'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Theme accentColor="blue" grayColor="slate" radius="large" scaling="100%">
      <App />
    </Theme>
  </StrictMode>,
)
