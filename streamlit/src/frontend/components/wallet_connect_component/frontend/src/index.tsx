import React from "react"
import { createRoot } from "react-dom/client"
import { StreamlitProvider } from "streamlit-component-lib-react-hooks"
import WalletConnect from "./WalletConnect"
import "./index.css"

const rootElement = document.getElementById("root")

if (rootElement) {
  const root = createRoot(rootElement)
  rootElement.style.background = "transparent"
  root.render(
    <React.StrictMode>
      <StreamlitProvider>
        <WalletConnect />
      </StreamlitProvider>
    </React.StrictMode>,
  )
}
