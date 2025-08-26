import { useEffect, useState } from 'react'
import {
  FaArrowCircleDown,
  FaRegClock,
  FaCog,
  FaPlusSquare,
  FaTasks,
  FaBars,
} from 'react-icons/fa'
import { Link, NavLink } from 'react-router'

export const Navbar = () => {
  const [upToDate, setUpToDate] = useState<boolean | null>(null)
  const [isBalena, setIsBalena] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [isNavCollapsed, setIsNavCollapsed] = useState(true)

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
      } catch {
        setIsBalena(false)
        setUpToDate(false)
      } finally {
        setIsLoading(false)
      }
    }

    fetchData()
  }, [])

  const handleNavToggle = () => {
    setIsNavCollapsed(!isNavCollapsed)
  }

  const handleNavLinkClick = () => {
    setIsNavCollapsed(true)
  }

  return (
    <>
      <nav className="navbar navbar-dark navbar-expand-lg fixed-top bg-dark">
        <div className="container">
          <NavLink to="/" className="navbar-brand py-3">
            <img src="/static/img/logo-full.svg" alt="Anthias" />
          </NavLink>

          <button
            className="navbar-toggler shadow-none border-2 border-light"
            type="button"
            data-bs-toggle="collapse"
            data-bs-target="#navbarNav"
            aria-controls="navbarNav"
            aria-expanded={!isNavCollapsed}
            aria-label="Toggle navigation"
            onClick={handleNavToggle}
          >
            <FaBars className="text-light" />
          </button>

          <div
            className={`collapse navbar-collapse ${!isNavCollapsed ? 'show' : ''}`}
            id="navbarNav"
          >
            <ul className="navbar-nav ms-auto">
              {!isLoading && !upToDate && !isBalena && (
                <li className="nav-item update-available">
                  <Link
                    to="/settings#upgrade-section"
                    className="nav-link"
                    onClick={handleNavLinkClick}
                  >
                    <span className="pe-1">
                      <FaArrowCircleDown />
                    </span>
                    <span className="d-none d-lg-inline">Update Available</span>
                    <span className="d-lg-none">Update</span>
                  </Link>
                </li>
              )}

              <li className="nav-item">
                <NavLink
                  to="/"
                  className="nav-link"
                  onClick={handleNavLinkClick}
                >
                  <span className="pe-1">
                    <FaRegClock />
                  </span>
                  <span className="d-none d-lg-inline">Schedule Overview</span>
                  <span className="d-lg-none">Schedule</span>
                </NavLink>
              </li>

              {isBalena && (
                <li className="nav-item">
                  <NavLink
                    to="/integrations"
                    className="nav-link"
                    onClick={handleNavLinkClick}
                  >
                    <span className="pe-1">
                      <FaPlusSquare />
                    </span>
                    <span className="d-none d-lg-inline">Integrations</span>
                    <span className="d-lg-none">Integrations</span>
                  </NavLink>
                </li>
              )}

              <li className="nav-item">
                <NavLink
                  to="/settings"
                  className="nav-link"
                  onClick={handleNavLinkClick}
                >
                  <span className="pe-1">
                    <FaCog />
                  </span>
                  <span className="d-none d-lg-inline">Settings</span>
                  <span className="d-lg-none">Settings</span>
                </NavLink>
              </li>

              <li className="nav-item d-none d-lg-block">
                <div className="divider-vertical"></div>
              </li>

              <li className="nav-item">
                <NavLink
                  to="/system-info"
                  className="nav-link"
                  onClick={handleNavLinkClick}
                >
                  <span className="pe-1">
                    <FaTasks />
                  </span>
                  <span className="d-none d-lg-inline">System Info</span>
                  <span className="d-lg-none">System Info</span>
                </NavLink>
              </li>
            </ul>
          </div>
        </div>
      </nav>
    </>
  )
}
