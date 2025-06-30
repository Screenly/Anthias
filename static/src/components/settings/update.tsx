import { useEffect, useState } from 'react';

export const Update = () => {
  const [ipAddresses, setIpAddresses] = useState<string[]>([]);

  useEffect(() => {
    // TODO: Fetch IP addresses from the API.
    setIpAddresses(['192.168.1.100', '192.168.1.101']);
  }, []);

  return (
    <>
      <div className="row py-2 mt-4">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>Update Anthias</b>
          </h4>
        </div>
      </div>
      <div className="row content px-3">
        <div id="upgrade-section" className="col-12 my-3">
          <p>Do the following steps to update Anthias:</p>
          <ul>
            <li>
              Go to the{' '}
              <a href="#backup-section" className="text-danger">
                backup section
              </a>{' '}
              and click <em>Get Backup</em>.
            </li>
            <li>
              Open up a terminal and SSH to this device using any of the
              following commands:
              <ul>
                {ipAddresses.map((ipAddress, index) => (
                  <li key={index}>
                    <code>ssh {ipAddress}</code>
                  </li>
                ))}
              </ul>
            </li>
            <li>
              Go to the project root directory &mdash;{' '}
              <code>cd ~/screenly</code>
            </li>
            <li>
              Run the following upgrade script &mdash;{' '}
              <code>./bin/run_upgrade.sh</code>. The script is essentially a
              wrapper around the install script, so it will prompt you with the
              same questions as when you first installed Anthias.
            </li>
          </ul>
        </div>
      </div>
    </>
  );
};
