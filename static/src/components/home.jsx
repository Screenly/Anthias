import classNames from 'classnames'
import { useEffect, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { fetchAssets, selectActiveAssets, selectInactiveAssets } from '@/store/assets-slice'

import { EmptyAssetMessage } from '@/components/empty-asset-message'
import { InactiveAssetsTable } from '@/components/inactive-assets'
import { ActiveAssetsTable } from '@/components/active-assets'

export const ScheduleOverview = () => {
  const dispatch = useDispatch()
  const activeAssets = useSelector(selectActiveAssets)
  const inactiveAssets = useSelector(selectInactiveAssets)

  useEffect(() => {
    document.title = 'Schedule Overview'
    dispatch(fetchAssets())
  }, [dispatch])

  // TODO: Get the player name from the server via API.
  const [playerName] = useState('')

  return (
    <>
      <div className="container pt-3 pb-3">
        <div className="row">
          <div className="col-12">
            <h4 className="d-flex">
              <b
                className={classNames(
                  'justify-content-center',
                  'align-self-center',
                  'text-white'
                )}
              >
                Schedule Overview
              </b>
              <div className="ml-auto">
                <a
                  id="previous-asset-button"
                  className={classNames(
                    'btn',
                    'btn-long',
                    'btn-outline-primary',
                    'mr-1',
                  )}
                  href="#"
                >
                  <i className="fas fa-chevron-left pr-2"></i>
                  Previous Asset
                </a>
                <a
                  id="next-asset-button"
                  className={classNames(
                    'btn',
                    'btn-long',
                    'btn-outline-primary',
                    'mr-1',
                  )}
                  href="#"
                >
                  Next Asset
                  <i className="fas fa-chevron-right pl-2"></i>
                </a>
                <a
                  id="add-asset-button"
                  className={classNames(
                    'add-asset-button',
                    'btn',
                    'btn-long',
                    'btn-primary',
                    'mr-1',
                  )}
                  href="#"
                >
                  Add Asset
                </a>
              </div>
            </h4>

            {
              playerName && (
                <h4 className="text-white">{ playerName }</h4>
              )
            }
          </div>
        </div>
      </div>

      <span id="assets">
        <div className="container">
          <div className="row content active-content px-2 pt-4">
            <div className="col-12 mb-5">
              <section id="active-assets-section">
                <h5>
                  <b>Active assets</b>
                </h5>
                <ActiveAssetsTable />
                {
                  activeAssets.length === 0 && (
                    <EmptyAssetMessage />
                  )
                }
              </section>
            </div>
          </div>
        </div>

        <div className="container mt-4">
          <div className="row content inactive-content px-2 pt-4">
            <div className="col-12 mb-5">
              <section id="inactive-assets-section">
                <h5>
                  <b>Inactive assets</b>
                </h5>
                <InactiveAssetsTable />
                {
                  inactiveAssets.length === 0 && (
                    <EmptyAssetMessage />
                  )
                }
              </section>
            </div>
          </div>
        </div>
      </span>
    </>
  )
}
