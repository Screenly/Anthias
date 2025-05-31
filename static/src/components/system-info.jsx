import { useEffect, useState } from 'react'

const ANTHIAS_REPO_URL = 'https://github.com/Screenly/Anthias'

const AnthiasVersionValue = ({ version }) => {
  const [commitLink, setCommitLink] = useState('')

  useEffect(() => {
    if (!version) {
      return
    }

    const [gitBranch, gitCommit] = version ? version.split('@') : ['', '']

    if (gitBranch === 'master') {
      setCommitLink(`${ANTHIAS_REPO_URL}/commit/${gitCommit}`)
    }
  })

  if (commitLink) {
    return (
      <a href={commitLink} rel="noopener" target="_blank" class="text-dark">
        {version}
      </a>
    )
  }

  return <>{version}</>
}

const Skeleton = ({ children, isLoading }) => {
  return isLoading ? (
    <span className="placeholder placeholder-wave"></span>
  ) : (
    children
  )
}

export const SystemInfo = () => {
  const [loadAverage, setLoadAverage] = useState('')
  const [freeSpace, setFreeSpace] = useState('')
  const [memory, setMemory] = useState({})
  const [uptime, setUptime] = useState({})
  const [displayPower, setDisplayPower] = useState(null)
  const [deviceModel, setDeviceModel] = useState('')
  const [anthiasVersion, setAnthiasVersion] = useState('')
  const [macAddress, setMacAddress] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const initializeSystemInfo = async () => {
    setIsLoading(true)

    const response = await fetch('/api/v2/info', {
      headers: {
        'Content-Type': 'application/json',
      },
    })
    if (!response.ok) {
      setIsLoading(false)
      throw new Error('Failed to fetch system info')
    }

    const systemInfo = await response.json()

    setIsLoading(false)

    setLoadAverage(systemInfo.loadavg)
    setFreeSpace(systemInfo.free_space)
    setMemory(systemInfo.memory)
    setUptime(systemInfo.uptime)
    setDisplayPower(systemInfo.display_power)
    setDeviceModel(systemInfo.device_model)
    setAnthiasVersion(systemInfo.anthias_version)
    setMacAddress(systemInfo.mac_address)
  }

  useEffect(() => {
    document.title = 'System Info'
    initializeSystemInfo()
  }, [])

  return (
    <div className="container">
      <div className="row py-2">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>System Info</b>
          </h4>
        </div>
      </div>
      <div className="row content">
        <div className="col-12">
          <table className="table mb-5">
            <thead className="table-borderless">
              <tr>
                <th
                  className="text-secondary font-weight-normal"
                  scope="col"
                  style={{ width: '20%' }}
                >
                  Option
                </th>
                <th className="text-secondary font-weight-normal" scope="col">
                  Value
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">Load Average</th>
                <td>
                  <Skeleton isLoading={isLoading}>{loadAverage}</Skeleton>
                </td>
              </tr>
              <tr>
                <th scope="row">Free Space</th>
                <td>
                  <Skeleton isLoading={isLoading}>{freeSpace}</Skeleton>
                </td>
              </tr>
              <tr>
                <th scope="row">Memory</th>
                <td>
                  <Skeleton isLoading={isLoading}>
                    <div>
                      Total: <strong>{memory.total} MB</strong>
                    </div>
                    <div>
                      Used: <strong>{memory.used} MB</strong>
                    </div>
                    <div>
                      Free: <strong>{memory.free} MB</strong>
                    </div>
                    <div>
                      Shared: <strong>{memory.shared} MB</strong>
                    </div>
                    <div>
                      Buff: <strong>{memory.buff} MB</strong>
                    </div>
                    <div>
                      Available: <strong>{memory.available} MB</strong>
                    </div>
                  </Skeleton>
                </td>
              </tr>
              <tr>
                <th scope="row">Uptime</th>
                <td>
                  <Skeleton isLoading={isLoading}>
                    {uptime.days} days and {uptime.hours} hours
                  </Skeleton>
                </td>
              </tr>
              <tr>
                <th scope="row">Display Power (CEC)</th>
                <td>
                  <Skeleton isLoading={isLoading}>
                    {displayPower || 'None'}
                  </Skeleton>
                </td>
              </tr>
              <tr>
                <th scope="row">Device Model</th>
                <td>
                  <Skeleton isLoading={isLoading}>{deviceModel}</Skeleton>
                </td>
              </tr>
              <tr>
                <th scope="row">Anthias Version</th>
                <td>
                  <Skeleton isLoading={isLoading}>
                    <AnthiasVersionValue version={anthiasVersion} />
                  </Skeleton>
                </td>
              </tr>
              <tr>
                <th scope="row">MAC Address</th>
                <td>
                  <Skeleton isLoading={isLoading}>{macAddress}</Skeleton>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
