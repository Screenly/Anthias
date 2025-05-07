import classNames from 'classnames'
import { useEffect, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import $ from 'jquery'
import 'bootstrap/js/dist/tooltip'
import {
  fetchAssets,
  selectActiveAssets,
  selectInactiveAssets,
} from '@/store/assets'

import { EmptyAssetMessage } from '@/components/empty-asset-message'
import { InactiveAssetsTable } from '@/components/inactive-assets'
import { ActiveAssetsTable } from '@/components/active-assets'
import { AddAssetModal } from '@/components/add-asset-modal'
import { EditAssetModal } from '@/components/edit-asset-modal'

const tooltipStyles = `
  .tooltip {
    opacity: 1 !important;
    transition: opacity 0s ease-in-out !important;
  }
  .tooltip.fade {
    opacity: 0;
  }
  .tooltip.show {
    opacity: 1;
  }
  .tooltip-inner {
    background-color: #2c3e50;
    color: #fff;
    padding: 0.5rem 0.75rem;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    max-width: 300px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  }
  .tooltip.bs-tooltip-top .arrow::before {
    border-top-color: #2c3e50;
  }
`

export const ScheduleOverview = () => {
  const dispatch = useDispatch()
  const activeAssets = useSelector(selectActiveAssets)
  const inactiveAssets = useSelector(selectInactiveAssets)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [assetToEdit, setAssetToEdit] = useState(null)

  useEffect(() => {
    document.title = 'Schedule Overview'
    dispatch(fetchAssets())
  }, [dispatch])

  // Initialize tooltips
  useEffect(() => {
    const initializeTooltips = () => {
      $('[data-toggle="tooltip"]').tooltip({
        placement: 'top',
        trigger: 'hover',
        html: true,
        delay: { show: 0, hide: 0 },
        animation: true,
      })
    }

    // Initial tooltip initialization
    initializeTooltips()

    // Reinitialize tooltips when assets change
    const observer = new MutationObserver(() => {
      initializeTooltips()
    })

    // Observe changes in both active and inactive sections
    const activeSection = document.getElementById('active-assets-section')
    const inactiveSection = document.getElementById('inactive-assets-section')

    if (activeSection) {
      observer.observe(activeSection, { childList: true, subtree: true })
    }
    if (inactiveSection) {
      observer.observe(inactiveSection, { childList: true, subtree: true })
    }

    return () => {
      observer.disconnect()
      $('[data-toggle="tooltip"]').tooltip('dispose')
    }
  }, [activeAssets, inactiveAssets])

  // TODO: Get the player name from the server via API.
  const [playerName] = useState('')

  const handleAddAsset = (event) => {
    event.preventDefault()
    setIsModalOpen(true)
    setAssetToEdit(null)
  }

  const handleCloseModal = () => {
    setIsModalOpen(false)
  }

  const handleSaveAsset = () => {
    setIsModalOpen(false)
  }

  const handleEditAsset = (asset) => {
    setAssetToEdit(asset)
    setIsEditModalOpen(true)
  }

  const handleCloseEditModal = () => {
    setIsEditModalOpen(false)
    setAssetToEdit(null)
  }

  return (
    <>
      <style>{tooltipStyles}</style>
      <div className="container pt-3 pb-3">
        <div className="row">
          <div className="col-12">
            <h4 className="d-flex">
              <b
                className={classNames(
                  'justify-content-center',
                  'align-self-center',
                  'text-white',
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
                  onClick={handleAddAsset}
                >
                  Add Asset
                </a>
              </div>
            </h4>

            {playerName && <h4 className="text-white">{playerName}</h4>}
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
                <ActiveAssetsTable onEditAsset={handleEditAsset} />
                {activeAssets.length === 0 && (
                  <EmptyAssetMessage onAddAssetClick={handleAddAsset} />
                )}
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
                <InactiveAssetsTable onEditAsset={handleEditAsset} />
                {inactiveAssets.length === 0 && (
                  <EmptyAssetMessage onAddAssetClick={handleAddAsset} />
                )}
              </section>
            </div>
          </div>
        </div>
      </span>

      <AddAssetModal
        isOpen={isModalOpen}
        onClose={handleCloseModal}
        onSave={handleSaveAsset}
        initialData={assetToEdit}
      />

      <EditAssetModal
        isOpen={isEditModalOpen}
        onClose={handleCloseEditModal}
        asset={assetToEdit}
      />
    </>
  )
}
