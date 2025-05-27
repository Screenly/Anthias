import React, { useEffect } from 'react'
import classNames from 'classnames'
import { useDispatch, useSelector } from 'react-redux'
import { setActiveTab, selectAssetModalState } from '@/store/assets'
import { useFileUpload } from './use-file-upload'
import { useAssetForm } from './use-asset-form'
import { useModalAnimation } from './use-modal-animation'
import { UriTab } from './uri-tab'
import { FileUploadTab } from './file-upload-tab'

/**
 * Asset modal component
 * @param {Object} props - Component props
 * @param {boolean} props.isOpen - Whether the modal is open
 * @param {Function} props.onClose - Callback function to call after closing
 * @param {Function} props.onSave - Callback function to call after saving
 * @param {Object} props.initialData - Initial data for the form
 * @returns {JSX.Element|null} - Asset modal component
 */
export const AssetModal = ({ isOpen, onClose, onSave, initialData = {} }) => {
  const dispatch = useDispatch()
  const { activeTab, statusMessage, uploadProgress } = useSelector(
    selectAssetModalState,
  )

  // Use custom hooks
  const {
    fileInputRef,
    dropZoneRef,
    handleFileSelect,
    handleFileDrop,
    handleDragOver,
    handleDragEnter,
    handleDragLeave,
  } = useFileUpload()

  const {
    formData,
    isValid,
    errorMessage,
    isSubmitting,
    handleInputChange,
    handleSubmit,
  } = useAssetForm(onSave, onClose)

  const { isVisible, modalRef, handleClose } = useModalAnimation(
    isOpen,
    onClose,
  )

  // Reset form data when modal is opened
  useEffect(() => {
    if (isOpen) {
      // Form reset is handled by the useAssetForm hook
    }
  }, [isOpen, initialData])

  if (!isOpen && !isVisible) return null

  return (
    <div
      className={classNames('modal', {
        show: isOpen,
        fade: true,
        'd-block': isOpen,
        'modal-visible': isVisible,
      })}
      aria-hidden="true"
      role="dialog"
      tabIndex="-1"
      style={{
        display: isOpen ? 'block' : 'none',
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        transition: 'opacity 0.3s ease-in-out',
        opacity: isVisible ? 1 : 0,
      }}
    >
      <div
        className="modal-dialog"
        role="document"
        ref={modalRef}
        style={{
          transition: 'transform 0.3s ease-in-out',
          transform: isVisible ? 'translate(0, 0)' : 'translate(0, -25%)',
        }}
      >
        <div className="modal-content">
          <div className="form-horizontal">
            <div className="modal-header">
              <h3 id="modalLabel">Add Asset</h3>
              <button type="button" className="close" onClick={handleClose}>
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div className="modal-body">
              <div className="asset-location add">
                <fieldset>
                  <div className="tabbable">
                    <ul className="nav nav-tabs" id="add-asset-nav-tabs">
                      <li
                        className={classNames(
                          'tabnav-uri nav-item text-center',
                          { active: activeTab === 'uri' },
                        )}
                      >
                        <a
                          className="nav-link"
                          href="#"
                          onClick={() => dispatch(setActiveTab('uri'))}
                        >
                          URL
                        </a>
                      </li>
                      <li
                        className={classNames(
                          'tabnav-file_upload nav-item text-center',
                          { active: activeTab === 'file_upload' },
                        )}
                      >
                        <a
                          className="nav-link upload-asset-tab"
                          href="#"
                          onClick={() => dispatch(setActiveTab('file_upload'))}
                        >
                          Upload
                        </a>
                      </li>
                    </ul>
                    <div className="tab-content px-4 pt-2 pb-4">
                      {activeTab === 'uri' ? (
                        <UriTab
                          formData={formData}
                          isValid={isValid}
                          errorMessage={errorMessage}
                          isSubmitting={isSubmitting}
                          handleInputChange={handleInputChange}
                        />
                      ) : (
                        <FileUploadTab
                          fileInputRef={fileInputRef}
                          dropZoneRef={dropZoneRef}
                          handleFileSelect={handleFileSelect}
                          handleFileDrop={handleFileDrop}
                          handleDragOver={handleDragOver}
                          handleDragEnter={handleDragEnter}
                          handleDragLeave={handleDragLeave}
                          isSubmitting={isSubmitting}
                          uploadProgress={uploadProgress}
                        />
                      )}
                    </div>
                  </div>
                </fieldset>
              </div>
            </div>
            <div className="modal-footer">
              <div
                className="status"
                style={{
                  display:
                    statusMessage && activeTab === 'file_upload'
                      ? 'block'
                      : 'none',
                }}
              >
                {statusMessage}
              </div>
              <button
                className="btn btn-outline-primary btn-long cancel"
                type="button"
                onClick={handleClose}
                disabled={isSubmitting}
              >
                Back to Assets
              </button>
              {activeTab === 'uri' && (
                <button
                  id="save-asset"
                  className="btn btn-primary btn-long"
                  type="submit"
                  onClick={handleSubmit}
                  disabled={isSubmitting || !isValid}
                >
                  Save
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
