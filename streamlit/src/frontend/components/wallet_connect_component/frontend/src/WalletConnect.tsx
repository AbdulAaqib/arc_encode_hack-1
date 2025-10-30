import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Streamlit } from "streamlit-component-lib"
import { useRenderData } from "streamlit-component-lib-react-hooks"

declare global {
  interface Window {
    ethereum?: any
  }
}

function shorten(addr?: string): string {
  if (!addr) {
    return ""
  }
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`
}

type WalletInfo = {
  address?: string
  chainId?: string
  isConnected: boolean
  error?: string
}

type ChainMetadata = {
  chainName: string
  rpcUrls: string[]
  blockExplorerUrls?: string[]
  nativeCurrency: {
    name: string
    symbol: string
    decimals: number
  }
}

const CHAIN_METADATA: Record<number, ChainMetadata> = {
  5042002: {
    chainName: "Arc Testnet",
    rpcUrls: ["https://rpc.testnet.arc.network"],
    blockExplorerUrls: ["https://testnet.arcscan.app"],
    nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 18 },
  },
  80002: {
    chainName: "Polygon PoS Amoy",
    rpcUrls: ["https://rpc-amoy.polygon.technology"],
    blockExplorerUrls: ["https://amoy.polygonscan.com"],
    nativeCurrency: { name: "MATIC", symbol: "MATIC", decimals: 18 },
  },
}

function getChainMetadata(chainId?: number): ChainMetadata | undefined {
  if (chainId === undefined || chainId === null) {
    return undefined
  }
  return CHAIN_METADATA[chainId]
}

function buildTxUrl(chainId: number | undefined, txHash: string): string | undefined {
  if (!chainId) return undefined
  const metadata = getChainMetadata(chainId)
  const base = metadata?.blockExplorerUrls?.[0]
  if (!base) return undefined
  const trimmed = base.endsWith("/") ? base.slice(0, -1) : base
  return `${trimmed}/tx/${txHash}`
}

export default function WalletConnect(): JSX.Element {
  const renderData = useRenderData()
  const disabled = !!renderData.disabled
  const theme = renderData.theme
  const requireChainId = renderData.args?.["require_chain_id"] as
    | number
    | string
    | undefined
  const txRequest = renderData.args?.["tx_request"] as any
  const action = (renderData.args?.["action"] as string | undefined) ?? undefined
  const txLabel = (renderData.args?.["tx_label"] as string | undefined) ?? undefined
  const preferredAddress = renderData.args?.["preferred_address"] as string | undefined
  const autoConnect = !!renderData.args?.["autoconnect"]
  const mode = (renderData.args?.["mode"] as string | undefined) ?? "interactive"
  const command = renderData.args?.["command"] as string | undefined
  const commandPayload = renderData.args?.["command_payload"] as any
  const commandSequence = renderData.args?.["command_sequence"] as number | undefined
  
  const [info, setInfo] = useState<WalletInfo>({ isConnected: false })
  const [sending, setSending] = useState<boolean>(false)
  const [txResult, setTxResult] = useState<any>(undefined)
  
  const setValue = useCallback((payload: unknown) => {
    Streamlit.setComponentValue(payload)
  }, [])

  const handleAccountsChanged = useCallback(
    (accounts: string[]) => {
      if (accounts && accounts.length > 0) {
        const address = accounts[0]
        setInfo(prev => ({ ...prev, address, isConnected: true, error: undefined }))
        setValue({ address, chainId: info.chainId, isConnected: true })
      } else {
        setInfo({ isConnected: false })
        setValue({ isConnected: false })
      }
    },
    [info.chainId, setValue],
  )

  const handleChainChanged = useCallback(
    (chainId: string) => {
      setInfo(prev => ({ ...prev, chainId }))
      setValue({ address: info.address, chainId, isConnected: !!info.address })
    },
    [info.address, setValue],
  )

  // Silent autoconnect to previously authorized accounts
  useEffect(() => {
    if (!autoConnect) return
    const eth = window.ethereum
    if (!eth) return
    (async () => {
      try {
        const accounts: string[] = await eth.request({ method: "eth_accounts" })
        const chainId: string = await eth.request({ method: "eth_chainId" })
        const address = accounts?.[0]
        if (address) {
          setInfo({ address, chainId, isConnected: true })
          setValue({ address, chainId, isConnected: true })
        }
      } catch {
        // ignore
      }
    })()
  }, [autoConnect, setValue])

  const connect = useCallback(async () => {
    const eth = window.ethereum
    if (!eth) {
      const msg = "No injected wallet found. Install MetaMask to continue."
      setInfo({ isConnected: false, error: msg })
      setValue({ isConnected: false, error: msg })
      return
    }

    try {
      const accounts: string[] = await eth.request({ method: "eth_requestAccounts" })
      const chainId: string = await eth.request({ method: "eth_chainId" })
      const address = accounts?.[0]
      if (address) {
        setInfo({ address, chainId, isConnected: true })
        setValue({ address, chainId, isConnected: true })
      }
    } catch (e: any) {
      const msg = e?.message ?? String(e)
      setInfo({ isConnected: false, error: msg })
      setValue({ isConnected: false, error: msg })
    }
  }, [setValue])

  const disconnect = useCallback(() => {
    setInfo({ isConnected: false })
    setValue({ isConnected: false })
  }, [setValue])

  useEffect(() => {
    const eth = window.ethereum
    if (!(eth?.on)) {
      return
    }
    eth.on("accountsChanged", handleAccountsChanged)
    eth.on("chainChanged", handleChainChanged)
    return () => {
      eth?.removeListener?.("accountsChanged", handleAccountsChanged)
      eth?.removeListener?.("chainChanged", handleChainChanged)
    }
  }, [handleAccountsChanged, handleChainChanged])

  const button = useMemo(() => {
    if (info.isConnected) {
      return (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontFamily: "monospace" }}>{shorten(info.address)}</span>
          <button onClick={disconnect} disabled={disabled}>
            Disconnect
          </button>
        </div>
      )
    }
    return (
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button onClick={connect} disabled={disabled}>
          Connect Wallet
        </button>
        {!!preferredAddress && (
          <span style={{ fontSize: 12, color: "#888" }}>Cached: {shorten(preferredAddress)}</span>
        )}
      </div>
    )
  }, [info.isConnected, info.address, disconnect, connect, disabled, preferredAddress])

  const borderColor = theme?.primaryColor ?? "#ddd"

  function chainIdToNumber(id?: string | number): number | undefined {
    if (id === undefined || id === null) return undefined
    if (typeof id === "number") return id
    const s = String(id)
    try {
      if (s.startsWith("0x") || s.startsWith("0X")) {
        return parseInt(s, 16)
      }
      return parseInt(s, 10)
    } catch {
      return undefined
    }
  }
  const expectedNum = chainIdToNumber(requireChainId)
  const connectedNum = chainIdToNumber(info.chainId)
  const chainMismatch = useMemo(() => {
    if (!expectedNum || !connectedNum) return false
    return expectedNum !== connectedNum
  }, [expectedNum, connectedNum])

  const expectedHex = expectedNum ? "0x" + expectedNum.toString(16) : undefined
  const connectedHex = connectedNum ? "0x" + connectedNum.toString(16) : undefined
  const expectedMeta = expectedNum ? getChainMetadata(expectedNum) : undefined
  const connectedMeta = connectedNum ? getChainMetadata(connectedNum) : undefined

  function pretty(value: any): string {
    if (value === undefined || value === null) {
      return "null"
    }
    if (typeof value === "string") {
      try {
        return JSON.stringify(JSON.parse(value), null, 2)
      } catch {
        return value
      }
    }
    return JSON.stringify(value, null, 2)
  }

  const switchNetwork = useCallback(async () => {
    const eth = window.ethereum
    if (!eth || !expectedHex || !expectedNum) {
      return
    }
    const metadata = getChainMetadata(expectedNum)
    const chainAddParams = metadata
      ? [{
          chainId: expectedHex,
          chainName: metadata.chainName,
          nativeCurrency: metadata.nativeCurrency,
          rpcUrls: metadata.rpcUrls,
          blockExplorerUrls: metadata.blockExplorerUrls,
        }]
      : [{ chainId: expectedHex }]
    try {
      await eth.request({ method: "wallet_switchEthereumChain", params: [{ chainId: expectedHex }] })
    } catch (e: any) {
      const code = e?.code
      const msg = e?.message ?? String(e)
      if (code === 4902) {
        try {
          await eth.request({
            method: "wallet_addEthereumChain",
            params: chainAddParams,
          })
          await eth.request({ method: "wallet_switchEthereumChain", params: [{ chainId: expectedHex }] })
        } catch (addErr: any) {
          const addMsg = addErr?.message ?? String(addErr)
          const chainName = metadata?.chainName ?? `chain ${expectedHex}`
          Streamlit.setComponentValue({
            warning: `Unable to add/switch ${chainName}. Please add it manually in MetaMask.`,
            error: addMsg,
          })
          return
        }
      } else {
        Streamlit.setComponentValue({ error: msg })
        return
      }
    }
    try {
      const chainId: string = await eth.request({ method: "eth_chainId" })
      setInfo(prev => ({ ...prev, chainId }))
      setValue({ address: info.address, chainId, isConnected: !!info.address })
    } catch {
      // ignore if unable to fetch chain ID
    }
  }, [expectedHex, info.address, setValue])

  const sendTransaction = useCallback(
    async (override?: { request?: any; action?: string; from?: string; silent?: boolean }) => {
      const eth = window.ethereum
      if (!eth) {
        const msg = "No injected wallet found. Install MetaMask to continue."
        setTxResult({ error: msg })
        if (!override?.silent) {
          setValue({ isConnected: false, error: msg })
        }
        return { error: msg }
      }
      const method = override?.action || action || "eth_sendTransaction"
      const sourceRequest = override?.request ?? txRequest
      if (!sourceRequest) {
        const msg = "Transaction request missing."
        setTxResult({ error: msg })
        if (!override?.silent) {
          setValue({ address: info.address, chainId: info.chainId, isConnected: !!info.address, error: msg })
        }
        return { error: msg }
      }
      let fromAddr = override?.from ?? info.address
      if (!fromAddr) {
        try {
          const accounts: string[] = await eth.request({ method: "eth_accounts" })
          fromAddr = accounts?.[0]
        } catch (err: any) {
          const msg = err?.message ?? String(err)
          setTxResult({ error: msg })
          if (!override?.silent) {
            setValue({ isConnected: false, error: msg })
          }
          return { error: msg }
        }
      }
      if (!fromAddr) {
        const msg = "Wallet not connected; run connect first."
        setTxResult({ error: msg })
        if (!override?.silent) {
          setValue({ isConnected: false, error: msg })
        }
        return { error: msg }
      }
      const request = { ...sourceRequest, from: fromAddr }
      setSending(true)
      setTxResult(undefined)
      try {
        const txHash: string = await eth.request({ method, params: [request] })
        const payload = { address: fromAddr, chainId: info.chainId, isConnected: true, txHash, method }
        setTxResult({ txHash })
        if (!override?.silent) {
          setValue(payload)
        }
        return payload
      } catch (err: any) {
        const msg = err?.message ?? String(err)
        setTxResult({ error: msg })
        if (!override?.silent) {
          setValue({ address: info.address, chainId: info.chainId, isConnected: true, error: msg })
        }
        return { error: msg }
      } finally {
        setSending(false)
      }
    },
    [info.address, info.chainId, txRequest, action, setValue]
  )

  const lastCommandRef = useRef<number | undefined>()

  useEffect(() => {
    if (mode !== "headless") {
      return
    }
    if (!command || commandSequence === undefined) {
      return
    }
    if (lastCommandRef.current === commandSequence) {
      return
    }
    lastCommandRef.current = commandSequence

    const run = async () => {
      const eth = window.ethereum
      const base: Record<string, any> = { command, commandSequence }
      if (!eth) {
        const msg = "No injected wallet found. Install MetaMask to continue."
        setValue({ ...base, error: msg })
        return
      }
      try {
        switch (command) {
          case "connect": {
            const accounts: string[] = await eth.request({ method: "eth_requestAccounts" })
            const chainId: string = await eth.request({ method: "eth_chainId" })
            const address = accounts?.[0]
            if (!address) {
              throw new Error("No account returned from wallet.")
            }
            setInfo({ address, chainId, isConnected: true })
            setValue({ ...base, address, chainId, isConnected: true, status: "connected" })
            break
          }
          case "disconnect": {
            setInfo({ isConnected: false })
            setValue({ ...base, isConnected: false, status: "disconnected" })
            break
          }
          case "switch_network": {
            const targetChain = commandPayload?.require_chain_id ?? requireChainId
            const targetNum = chainIdToNumber(targetChain)
            if (!targetNum) {
              throw new Error("No target chain id supplied for switch_network command.")
            }
            const targetHex = "0x" + targetNum.toString(16)
            const metadata = getChainMetadata(targetNum)
            const chainAddParams = metadata
              ? [{
                  chainId: targetHex,
                  chainName: metadata.chainName,
                  nativeCurrency: metadata.nativeCurrency,
                  rpcUrls: metadata.rpcUrls,
                  blockExplorerUrls: metadata.blockExplorerUrls,
                }]
              : [{ chainId: targetHex }]
            try {
              await eth.request({ method: "wallet_switchEthereumChain", params: [{ chainId: targetHex }] })
            } catch (switchErr: any) {
              if (switchErr?.code === 4902) {
                await eth.request({
                  method: "wallet_addEthereumChain",
                  params: chainAddParams,
                })
                await eth.request({ method: "wallet_switchEthereumChain", params: [{ chainId: targetHex }] })
              } else {
                throw switchErr
              }
            }
            const finalChain: string = await eth.request({ method: "eth_chainId" })
            setInfo(prev => ({ ...prev, chainId: finalChain }))
            setValue({ ...base, chainId: finalChain, status: "switched" })
            break
          }
          case "send_transaction": {
            const payload = commandPayload || {}
            const requestSource = payload.tx_request ?? txRequest
            if (!requestSource) {
              throw new Error("send_transaction command missing tx_request")
            }
            const method = payload.action || action || "eth_sendTransaction"
            const result = await sendTransaction({ request: requestSource, action: method, from: payload.from, silent: true })
            if (result && "error" in result) {
              setValue({ ...base, error: result.error, status: "error" })
            } else {
              setValue({ ...base, ...(result || {}), status: "sent" })
            }
            break
          }
          default: {
            setValue({ ...base, error: `Unknown command: ${command}` })
          }
        }
      } catch (err: any) {
        const msg = err?.message ?? String(err)
        setValue({ ...base, error: msg })
      }
    }

    run()
  }, [mode, command, commandSequence, commandPayload, requireChainId, info.address, info.chainId, action, txRequest, setValue, sendTransaction])

  if (mode === "headless") {
    return <div style={{ display: "none" }} />
  }

  return (
    <div
      style={{
        fontFamily:
          "system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica, Arial, sans-serif",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        border: `1px solid ${borderColor}`,
        padding: 12,
        borderRadius: 8,
        background: "#fff",
        boxShadow: "0 1px 2px rgba(0,0,0,0.06)",
      }}
    >
      {button}
      {info.chainId && <div style={{ fontSize: 12, color: "#888" }}>Chain: {info.chainId}</div>}
      {chainMismatch && (
        <div style={{ fontSize: 12, color: "darkorange" }}>
          Expected chain {expectedMeta?.chainName ?? String(requireChainId)} ({expectedHex}), but connected to {connectedMeta?.chainName ?? String(info.chainId)} ({connectedHex})
          <div style={{ marginTop: 6 }}>
            <button onClick={switchNetwork} disabled={disabled}>Switch Network</button>
          </div>
        </div>
      )}
      {info.error && (
        <div style={{ marginTop: 6, color: "crimson", fontSize: 12 }}>{info.error}</div>
      )}
      {!!txRequest && (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 12, color: "#666" }}>Transaction prepared by server:</div>
          <pre style={{
            fontSize: 12,
            fontFamily:
              "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
            color: "#111",
            background: "#f7f7f7",
            padding: 10,
            borderRadius: 6,
            overflowX: "auto",
            border: `1px solid ${borderColor}`,
          }}>{pretty(txRequest)}</pre>
          <button onClick={() => sendTransaction()} disabled={disabled || sending || !info.isConnected || chainMismatch}>
            {sending ? "Sending…" : (txLabel ?? (action || "Send Transaction"))}
          </button>
          {txResult && (
            <div style={{ fontSize: 12 }}>
              {txResult.txHash ? (
                (() => {
                  const txUrl = buildTxUrl(connectedNum, txResult.txHash)
                  return (
                    <span>
                      Sent. Tx hash: <code>{txResult.txHash}</code>
                      {' '}
                      {txUrl && (
                        <>
                          ·{' '}
                          <a href={txUrl} target="_blank" rel="noreferrer">
                            View on Explorer
                          </a>
                        </>
                      )}
                    </span>
                  )
                })()
              ) : (
                <span style={{ color: "crimson" }}>Error: {String(txResult.error)}</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

