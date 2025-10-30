import React from "react"
import { createRoot } from "react-dom/client"
import { StreamlitProvider } from "streamlit-component-lib-react-hooks"
import WalletConnect from "./WalletConnect"

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <StreamlitProvider>
      <WalletConnect />
    </StreamlitProvider>
  </React.StrictMode>,
)
