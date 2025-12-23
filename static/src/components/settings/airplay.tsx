import { useEffect, useState } from 'react'
import { FaApple, FaWifi } from 'react-icons/fa'

interface AirPlayStatus {
  enabled: boolean
  name: string
  state: string
  client_name: string | null
}

export const AirPlay = () => {
  const [status, setStatus] = useState<AirPlayStatus | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [airplayName, setAirplayName] = useState('')
  const [airplayEnabled, setAirplayEnabled] = useState(true)

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [])

  const fetchStatus = async () => {
    try {
      const response = await fetch('/api/v2/airplay')
      const data = await response.json()
      setStatus(data)
      setAirplayName(data.name)
      setAirplayEnabled(data.enabled)
    } catch (error) {
      console.error('Failed to fetch AirPlay status:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleSave = async () => {
    setIsSaving(true)
    try {
      await fetch('/api/v2/airplay', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: airplayEnabled,
          name: airplayName,
        }),
      })
      await fetchStatus()
    } catch (error) {
      console.error('Failed to update AirPlay settings:', error)
    } finally {
      setIsSaving(false)
    }
  }

  const getStateLabel = (state: string) => {
    switch (state) {
      case 'streaming':
        return 'Streaming'
      case 'connected':
        return 'Connected'
      case 'idle':
        return 'Ready'
      default:
        return 'Unknown'
    }
  }

  const getStateClass = (state: string) => {
    switch (state) {
      case 'streaming':
        return 'text-success'
      case 'connected':
        return 'text-warning'
      case 'idle':
        return 'text-muted'
      default:
        return 'text-secondary'
    }
  }

  if (isLoading) {
    return (
      <div className="row content px-3 mt-4">
        <div className="col-12 my-3">
          <h5 className="text-white">
            <FaApple className="me-2" />
            AirPlay
          </h5>
          <div className="spinner-border spinner-border-sm" role="status">
            <span className="visually-hidden">Loading...</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="row content px-3 mt-4">
      <div className="col-12 my-3">
        <h5 className="text-white">
          <FaApple className="me-2" />
          AirPlay
        </h5>

        <div className="row">
          <div className="col-6">
            <div className="mb-3">
              <label className="form-label">Device Name</label>
              <input
                type="text"
                className="form-control"
                value={airplayName}
                onChange={(e) => setAirplayName(e.target.value)}
                placeholder="Checkin Cast"
              />
              <small className="text-muted">
                This name will appear on Apple devices when casting
              </small>
            </div>

            <div className="form-check form-switch mb-3">
              <input
                className="form-check-input"
                type="checkbox"
                id="airplayEnabled"
                checked={airplayEnabled}
                onChange={(e) => setAirplayEnabled(e.target.checked)}
              />
              <label className="form-check-label" htmlFor="airplayEnabled">
                Enable AirPlay receiver
              </label>
            </div>
          </div>

          <div className="col-6">
            <div className="card bg-dark border-secondary">
              <div className="card-body">
                <h6 className="card-title text-white">
                  <FaWifi className="me-2" />
                  Status
                </h6>
                <p className="mb-1">
                  <span className="text-muted">State: </span>
                  <span className={getStateClass(status?.state || 'unknown')}>
                    {getStateLabel(status?.state || 'unknown')}
                  </span>
                </p>
                {status?.client_name && (
                  <p className="mb-0">
                    <span className="text-muted">Client: </span>
                    <span className="text-white">{status.client_name}</span>
                  </p>
                )}
                {status?.state === 'idle' && (
                  <p className="mb-0 text-muted small mt-2">
                    Open Control Center on your iPhone/iPad and tap Screen
                    Mirroring to connect
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="text-end mt-3">
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={isSaving}
          >
            {isSaving ? (
              <span
                className="spinner-border spinner-border-sm"
                role="status"
              ></span>
            ) : (
              'Save AirPlay Settings'
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
