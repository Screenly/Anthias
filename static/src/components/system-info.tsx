import { useEffect, useState } from 'react';
import {
  AnthiasVersionValueProps,
  SkeletonProps,
  MemoryInfo,
  UptimeInfo,
} from '@/types';

const ANTHIAS_REPO_URL = 'https://github.com/Screenly/Anthias';

const AnthiasVersionValue = ({ version }: AnthiasVersionValueProps) => {
  const [commitLink, setCommitLink] = useState('');

  useEffect(() => {
    if (!version) {
      return;
    }

    const [gitBranch, gitCommit] = version ? version.split('@') : ['', ''];

    if (gitBranch === 'master') {
      setCommitLink(`${ANTHIAS_REPO_URL}/commit/${gitCommit}`);
    }
  });

  if (commitLink) {
    return (
      <a href={commitLink} rel="noopener" target="_blank" className="text-dark">
        {version}
      </a>
    );
  }

  return <>{version}</>;
};

const Skeleton = ({ children, isLoading }: SkeletonProps) => {
  return isLoading ? (
    <span className="placeholder placeholder-wave"></span>
  ) : (
    children
  );
};

export const SystemInfo = () => {
  const [loadAverage, setLoadAverage] = useState('');
  const [freeSpace, setFreeSpace] = useState('');
  const [memory, setMemory] = useState<MemoryInfo>({
    total: 0,
    used: 0,
    free: 0,
    shared: 0,
    buff: 0,
    available: 0,
  });
  const [uptime, setUptime] = useState<UptimeInfo>({
    days: 0,
    hours: 0,
  });
  const [displayPower, setDisplayPower] = useState<string | null>(null);
  const [deviceModel, setDeviceModel] = useState('');
  const [anthiasVersion, setAnthiasVersion] = useState('');
  const [macAddress, setMacAddress] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [playerName, setPlayerName] = useState('');

  const initializeSystemInfo = async () => {
    setIsLoading(true);

    try {
      const [infoResponse, settingsResponse] = await Promise.all([
        fetch('/api/v2/info', {
          headers: {
            'Content-Type': 'application/json',
          },
        }),
        fetch('/api/v2/device_settings'),
      ]);

      if (!infoResponse.ok) {
        throw new Error('Failed to fetch system info');
      }

      const [systemInfo, settingsData] = await Promise.all([
        infoResponse.json(),
        settingsResponse.json(),
      ]);

      setLoadAverage(systemInfo.loadavg);
      setFreeSpace(systemInfo.free_space);
      setMemory(systemInfo.memory);
      setUptime(systemInfo.uptime);
      setDisplayPower(systemInfo.display_power);
      setDeviceModel(systemInfo.device_model);
      setAnthiasVersion(systemInfo.anthias_version);
      setMacAddress(systemInfo.mac_address);
      setPlayerName(settingsData.player_name ?? '');
    } catch {
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    initializeSystemInfo();
  }, []);

  useEffect(() => {
    const title = playerName ? `${playerName} · System Info` : 'System Info';
    document.title = title;
  }, [playerName]);

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
                      Total: <strong>{memory.total} MiB</strong>
                    </div>
                    <div>
                      Used: <strong>{memory.used} MiB</strong>
                    </div>
                    <div>
                      Free: <strong>{memory.free} MiB</strong>
                    </div>
                    <div>
                      Shared: <strong>{memory.shared} MiB</strong>
                    </div>
                    <div>
                      Buff: <strong>{memory.buff} MiB</strong>
                    </div>
                    <div>
                      Available: <strong>{memory.available} MiB</strong>
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
  );
};
