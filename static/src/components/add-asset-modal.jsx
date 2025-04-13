import { useState, useEffect, useRef } from 'react'
import classNames from 'classnames'
import { useDispatch } from 'react-redux'
import { addAsset } from '@/store/assets-slice'

export const AddAssetModal = ({
  isOpen,
  onClose,
  onSave,
  initialData = {},
}) => {
  const dispatch = useDispatch()
  const [activeTab, setActiveTab] = useState('uri')
  const [isVisible, setIsVisible] = useState(false)
  const [isClosing, setIsClosing] = useState(false)
  const modalRef = useRef(null)
  const [formData, setFormData] = useState({
    uri: initialData?.uri || '',
    skipAssetCheck: initialData?.skipAssetCheck || false,
  })
  const [isValid, setIsValid] = useState(true)
  const [errorMessage, setErrorMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [statusMessage, setStatusMessage] = useState('')
  const [uploadProgress, setUploadProgress] = useState(0)
  const fileInputRef = useRef(null)
  const dropZoneRef = useRef(null)

  // TODO: Fetch default durations from settings, preferably via API.
  // Default duration values based on mimetype
  const defaultDuration = 60 // 60 seconds for webpage
  const defaultStreamingDuration = 3600 // 1 hour for streaming

  // Reset form data when modal is opened
  useEffect(() => {
    if (isOpen) {
      setFormData({
        uri: initialData?.uri || '',
        skipAssetCheck: initialData?.skipAssetCheck || false,
      })
      setIsValid(true)
      setErrorMessage('')
      setStatusMessage('')
      setIsClosing(false)
    }
  }, [isOpen, initialData])

  // Handle animation when modal opens/closes
  useEffect(() => {
    if (isOpen) {
      // Small delay to ensure the DOM is updated before adding the visible class
      setTimeout(() => {
        setIsVisible(true)
      }, 10)
    } else {
      setIsVisible(false)
    }
  }, [isOpen])

  // Handle clicks outside the modal
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (modalRef.current && !modalRef.current.contains(event.target)) {
        handleClose()
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  // Handle file selection
  const handleFileSelect = (e) => {
    const file = e.target.files[0]
    if (file) {
      handleFileUpload(file)
    }
  }

  // Handle file drop
  const handleFileDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()

    const file = e.dataTransfer.files[0]
    if (file) {
      handleFileUpload(file)
    }
  }

  // Handle drag events
  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDragEnter = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  // Main file upload function
  const handleFileUpload = async (file) => {
    setStatusMessage('')
    setIsSubmitting(true)
    setUploadProgress(0) // Reset progress at the start

    const filename = file.name
    const mimetype = getMimetype(filename)

    // Update form data with file name
    setFormData((prev) => ({
      ...prev,
      name: filename,
    }))

    try {
      // Create FormData and append file
      const formData = new FormData()
      formData.append('file_upload', file)

      // Upload file using Fetch API with progress tracking
      const xhr = new XMLHttpRequest()

      // Create a promise to handle the XHR
      const uploadPromise = new Promise((resolve, reject) => {
        xhr.upload.addEventListener('progress', (e) => {
          if (e.lengthComputable) {
            const progress = Math.round((e.loaded / e.total) * 100)
            setUploadProgress(progress)
          }
        })

        xhr.addEventListener('load', () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const response = JSON.parse(xhr.responseText)
              resolve(response)
            } catch (error) {
              reject(new Error('Invalid JSON response'))
            }
          } else {
            reject(new Error(`Upload failed with status ${xhr.status}`))
          }
        })

        xhr.addEventListener('error', () => {
          reject(new Error('Network error during upload'))
        })

        xhr.addEventListener('abort', () => {
          reject(new Error('Upload aborted'))
        })
      })

      // Start the upload
      xhr.open('POST', '/api/v2/file_asset')
      xhr.send(formData)

      // Wait for upload to complete
      const response = await uploadPromise

      // Create asset data
      const assetData = {
        uri: response.uri,
        ext: response.ext,
        name: filename,
        mimetype,
        is_active: 1,
        is_enabled: 0,
        is_processing: 0,
        nocache: 0,
        play_order: 0,
        duration: getDurationForMimetype(mimetype),
        skip_asset_check: formData.skipAssetCheck ? 1 : 0,
        ...getDefaultDates(),
      }

      // Make API call to save the asset
      const saveResponse = await fetch('/api/v2/assets', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(assetData),
      })

      if (!saveResponse.ok) {
        throw new Error('Failed to save asset')
      }

      const data = await saveResponse.json()

      // Create the complete asset object with the response data
      const completeAsset = {
        ...assetData,
        asset_id: data.asset_id,
        ...data,
      }

      // Dispatch the addAsset action to update the Redux store
      dispatch(addAsset(completeAsset))

      // Don't call onSave for file uploads to prevent modal from closing
      // onSave(completeAsset)

      // Reset form and show success message
      resetForm()
      setStatusMessage('Upload completed.')
      setIsSubmitting(false)
      setUploadProgress(0) // Reset progress after completion

      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      // Hide status message after 5 seconds
      setTimeout(() => {
        setStatusMessage('')
      }, 5000)
    } catch (error) {
      setErrorMessage(`Upload failed: ${error.message}`)
      setIsSubmitting(false)
      setUploadProgress(0) // Reset progress on error

      // Reset the progress bar width directly
      const progressBar = document.querySelector('.progress .bar')
      if (progressBar) {
        progressBar.style.width = '0%'
      }
    }
  }

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    setFormData({
      ...formData,
      [name]: type === 'checkbox' ? checked : value,
    })

    // Validate URL when it changes
    if (name === 'uri' && activeTab === 'uri') {
      validateUrl(value)
    }
  }

  const validateUrl = (url) => {
    if (!url) {
      setIsValid(true)
      setErrorMessage('')
      return
    }

    // URL validation pattern from anthias.coffee
    const urlPattern =
      /(http|https|rtsp|rtmp):\/\/[\w-]+(\.?[\w-]+)+([\w.,@?^=%&amp;:\/~+#-]*[\w@?^=%&amp;\/~+#-])?/
    const isValidUrl = urlPattern.test(url)

    setIsValid(isValidUrl)
    setErrorMessage(isValidUrl ? '' : 'Please enter a valid URL')
  }

  const getMimetype = (filename) => {
    // Implementation based on anthias.coffee
    const viduris = ['rtsp', 'rtmp']
    const mimetypes = [
      [['jpe', 'jpg', 'jpeg', 'png', 'pnm', 'gif', 'bmp'], 'image'],
      [['avi', 'mkv', 'mov', 'mpg', 'mpeg', 'mp4', 'ts', 'flv'], 'video'],
    ]
    const domains = [[['www.youtube.com', 'youtu.be'], 'youtube_asset']]

    // Check if it's a streaming URL
    const scheme = filename.split(':')[0].toLowerCase()
    if (viduris.includes(scheme)) {
      return 'streaming'
    }

    // Check if it's a domain-specific asset
    try {
      const domain = filename.split('//')[1].toLowerCase().split('/')[0]
      for (const [domainList, type] of domains) {
        if (domainList.includes(domain)) {
          return type
        }
      }
    } catch (e) {
      // Invalid URL format
    }

    // Check file extension
    try {
      const ext = filename.split('.').pop().toLowerCase()
      for (const [extList, type] of mimetypes) {
        if (extList.includes(ext)) {
          return type
        }
      }
    } catch (e) {
      // No extension found
    }

    // Default to webpage
    return 'webpage'
  }

  const getDurationForMimetype = (mimetype) => {
    // Implementation based on anthias.coffee change_mimetype method
    if (mimetype === 'video') {
      return 0
    } else if (mimetype === 'streaming') {
      return defaultStreamingDuration
    } else {
      return defaultDuration
    }
  }

  const getDefaultDates = () => {
    // Implementation based on anthias.coffee initialize method
    const now = new Date()
    const endDate = new Date()
    endDate.setDate(endDate.getDate() + 30) // 30 days from now

    return {
      start_date: now.toISOString(),
      end_date: endDate.toISOString(),
    }
  }

  const resetForm = () => {
    setFormData({
      uri: '',
      skipAssetCheck: false,
    })
    setIsValid(true)
    setErrorMessage('')
    setStatusMessage('')
    setIsSubmitting(false)
  }

  const handleSubmit = async (e) => {
    e.preventDefault()

    if (activeTab === 'uri') {
      if (!formData.uri) {
        setErrorMessage('Please enter a URL')
        setIsValid(false)
        return
      }

      if (!isValid) {
        return
      }

      // Determine mimetype based on URL
      const mimetype = getMimetype(formData.uri)

      // Get duration based on mimetype
      const duration = getDurationForMimetype(mimetype)

      // Get default dates
      const { start_date, end_date } = getDefaultDates()

      // Create asset data
      const assetData = {
        ...formData,
        mimetype,
        name: formData.uri, // Use URI as name by default
        is_active: 1,
        is_enabled: 0,
        is_processing: 0,
        nocache: 0,
        play_order: 0,
        skip_asset_check: formData.skipAssetCheck ? 1 : 0,
        duration,
        start_date,
        end_date,
      }

      // Disable inputs during submission
      setIsSubmitting(true)

      try {
        // Make API call to save the asset
        const response = await fetch('/api/v2/assets', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(assetData),
        })

        if (!response.ok) {
          throw new Error('Failed to save asset')
        }

        const data = await response.json()

        // Create the complete asset object with the response data
        const completeAsset = {
          ...assetData,
          asset_id: data.asset_id,
          ...data,
        }

        // Dispatch the addAsset action to update the Redux store
        dispatch(addAsset(completeAsset))

        // Call the onSave callback with the asset data
        onSave(completeAsset)

        // Reset form
        resetForm()

        // Start the closing animation
        setIsClosing(true)

        // Wait for animation to complete before calling onClose
        setTimeout(() => {
          setIsClosing(false)
          onClose()
        }, 300) // Bootstrap's default fade duration
      } catch (error) {
        setErrorMessage('Failed to save asset. Please try again.')
        setIsSubmitting(false)
      }
    } else if (activeTab === 'file_upload') {
      // File upload handling would go here
      // This would be implemented in a future update
      setIsSubmitting(true)
      setStatusMessage('Uploading file...')

      // Simulate file upload process
      // In a real implementation, this would handle the file upload
      setTimeout(() => {
        setStatusMessage('Upload completed.')
        setIsSubmitting(false)

        // Hide status message after 5 seconds
        setTimeout(() => {
          setStatusMessage('')
        }, 5000)
      }, 2000)
    }
  }

  const handleClose = () => {
    // Start the closing animation
    setIsVisible(false)
    // Wait for animation to complete before calling onClose
    setTimeout(() => {
      onClose()
    }, 300) // Bootstrap's default fade duration
  }

  if (!isOpen && !isVisible && !isClosing) return null

  return (
    <div
      className={classNames('modal', {
        show: isOpen || isClosing,
        fade: true,
        'd-block': isOpen || isClosing,
        'modal-visible': isVisible,
        'modal-closing': isClosing,
      })}
      aria-hidden="true"
      role="dialog"
      tabIndex="-1"
      style={{
        display: isOpen || isClosing ? 'block' : 'none',
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
                          onClick={() => setActiveTab('uri')}
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
                          className="nav-link"
                          href="#"
                          onClick={() => setActiveTab('file_upload')}
                        >
                          Upload
                        </a>
                      </li>
                    </ul>
                    <div className="tab-content px-4 pt-2 pb-4">
                      <div
                        id="tab-uri"
                        className={classNames('tab-pane', {
                          active: activeTab === 'uri',
                        })}
                      >
                        <div className="form-group row uri">
                          <label className="col-4 col-form-label">
                            Asset URL
                          </label>
                          <div className="col-7 controls">
                            <input
                              className={classNames('form-control', {
                                'is-invalid': !isValid && formData.uri,
                              })}
                              name="uri"
                              value={formData.uri}
                              onChange={handleInputChange}
                              placeholder="Public URL to this asset's location"
                              type="text"
                              disabled={isSubmitting}
                            />
                            {!isValid && formData.uri && (
                              <div className="invalid-feedback">
                                {errorMessage}
                              </div>
                            )}
                          </div>
                        </div>
                        <div className="form-group row skip_asset_check_checkbox">
                          <label className="col-4 small">
                            Skip asset check
                          </label>
                          <div className="col-7 is_enabled-skip_asset_check_checkbox checkbox">
                            <input
                              name="skipAssetCheck"
                              type="checkbox"
                              checked={formData.skipAssetCheck}
                              onChange={handleInputChange}
                              disabled={isSubmitting}
                            />
                          </div>
                        </div>
                      </div>
                      <div
                        id="tab-file_upload"
                        className={classNames('tab-pane', {
                          active: activeTab === 'file_upload',
                        })}
                      >
                        <div className="control-group">
                          <div
                            className="filedrop"
                            ref={dropZoneRef}
                            onDrop={handleFileDrop}
                            onDragOver={handleDragOver}
                            onDragEnter={handleDragEnter}
                            onDragLeave={handleDragLeave}
                          >
                            <div className="upload-header">
                              <button
                                className="btn btn-primary"
                                onClick={() => fileInputRef.current.click()}
                              >
                                Add Files
                              </button>
                              <input
                                ref={fileInputRef}
                                name="file_upload"
                                type="file"
                                style={{ display: 'none' }}
                                onChange={handleFileSelect}
                              />
                              <br />
                              or
                            </div>
                            <div>drop files here to upload</div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </fieldset>
              </div>
            </div>
            <div className="modal-footer">
              <div
                className="status"
                style={{ display: statusMessage ? 'block' : 'none' }}
              >
                {statusMessage}
              </div>
              <div
                className="float-left progress active"
                style={{
                  display:
                    isSubmitting && activeTab === 'file_upload'
                      ? 'block'
                      : 'none',
                  width: '100%', // Match the original width from _styles.scss
                  marginTop: '4px',
                  marginBottom: '0px',
                  height: '20px', // Ensure the progress bar has height
                  backgroundColor: '#f5f5f5', // Light background for the progress bar container
                }}
              >
                <div
                  className="bar progress-bar-striped progress-bar progress-bar-animated"
                  style={{
                    width: `${uploadProgress}%`,
                    minWidth: '2%', // Ensure the progress bar is visible even at low percentages
                    backgroundColor: '#FFE11A', // Use the anthias-yellow-3 color
                    display: 'block', // Ensure the progress bar is visible
                    height: '100%', // Ensure the progress bar fills the container
                  }}
                ></div>
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
