import { useEffect, useState } from 'react'
import {
  FaArrowCircleDown,
  FaRegClock,
  FaCog,
  FaPlusSquare,
  FaTasks,
} from 'react-icons/fa'
import { Link, NavLink } from 'react-router'

export const Navbar = () => {
  const [upToDate, setUpToDate] = useState(null)
  const [isBalena, setIsBalena] = useState(false)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [integrationsResponse, infoResponse] = await Promise.all([
          fetch('/api/v2/integrations'),
          fetch('/api/v2/info'),
        ])

        const [integrationsData, infoData] = await Promise.all([
          integrationsResponse.json(),
          infoResponse.json(),
        ])

        setIsBalena(integrationsData.is_balena)
        setUpToDate(infoData.up_to_date)
      } catch (error) {
        setIsBalena(false)
        setUpToDate(false)
      } finally {
        setIsLoading(false)
      }
    }

    fetchData()
  }, [])

  return (
    <>
      <div className="navbar navbar-header navbar-expand-lg fixed-top bg-dark">
        <div className="container">
          <NavLink to="/" className="brand">
            <img src="/static/img/logo-full.svg" />
          </NavLink>
          <ul className="nav float-right">
            {!isLoading && !upToDate && !isBalena && (
              <li className="update-available">
                <Link to="/settings#upgrade-section">
                  <span className="pr-1">
                    <FaArrowCircleDown />
                  </span>
                  Update Available
                </Link>
              </li>
            )}

            <li>
              <NavLink to="/">
                <span className="pr-1">
                  <FaRegClock />
                </span>
                Schedule Overview
              </NavLink>
            </li>

            {isBalena && (
              <li>
                <NavLink to="/integrations">
                  <span className="pr-1">
                    <FaPlusSquare />
                  </span>
                  Integrations
                </NavLink>
              </li>
            )}

            <li>
              <NavLink to="/settings">
                <span className="pr-1">
                  <FaCog />
                </span>
                Settings
              </NavLink>
            </li>
            <li className="divider-vertical"></li>
            <li>
              <NavLink to="/system-info">
                <span className="pr-1">
                  <FaTasks />
                </span>
                System Info
              </NavLink>
            </li>
          </ul>
        </div>
      </div>
    </>
  )
}
