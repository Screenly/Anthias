import { useDispatch, useSelector } from 'react-redux'
import Swal from 'sweetalert2'

import { SWEETALERT_TIMER } from '@/constants'
import {
  createBackup,
  uploadBackup,
  resetUploadState,
  fetchSettings,
} from '@/store/settings'

export const Backup = () => {
  const dispatch = useDispatch()
  const { isUploading, uploadProgress } = useSelector((state) => state.settings)

  const handleBackup = async () => {
    const backupButton = document.getElementById('btn-backup')
    const originalText = backupButton.textContent
    backupButton.textContent = 'Preparing archive...'
    backupButton.disabled = true
    document.getElementById('btn-upload').disabled = true

    try {
      const result = await dispatch(createBackup()).unwrap()
      if (result) {
        window.location = `/static_with_mime/${result}?mime=application/x-tgz`
      }
    } catch (err) {
      await Swal.fire({
        title: 'Error!',
        text:
          err.message ||
          'The operation failed. Please reload the page and try again.',
        icon: 'error',
        customClass: {
          popup: 'swal2-popup',
          title: 'swal2-title',
          htmlContainer: 'swal2-html-container',
          confirmButton: 'swal2-confirm',
        },
      })
    } finally {
      backupButton.textContent = originalText
      backupButton.disabled = false
      document.getElementById('btn-upload').disabled = false
    }
  }

  const handleUpload = (e) => {
    e.preventDefault()
    const fileInput = document.querySelector('[name="backup_upload"]')
    fileInput.value = '' // Reset the file input
    fileInput.click()
  }

  const handleFileUpload = async (e) => {
    const file = e.target.files[0]
    if (!file) return

    document.getElementById('btn-upload').style.display = 'none'
    document.getElementById('btn-backup').style.display = 'none'
    document.querySelector('.progress').style.display = 'block'

    try {
      const result = await dispatch(uploadBackup(file)).unwrap()

      if (result) {
        await Swal.fire({
          title: 'Success!',
          text:
            typeof result === 'string'
              ? result
              : 'Backup uploaded successfully',
          icon: 'success',
          timer: SWEETALERT_TIMER,
          showConfirmButton: false,
          customClass: {
            popup: 'swal2-popup',
            title: 'swal2-title',
            htmlContainer: 'swal2-html-container',
          },
        })

        // Fetch updated settings after successful recovery
        dispatch(fetchSettings())
      }
    } catch (err) {
      await Swal.fire({
        title: 'Error!',
        text:
          err.message ||
          'The operation failed. Please reload the page and try again.',
        icon: 'error',
        customClass: {
          popup: 'swal2-popup',
          title: 'swal2-title',
          htmlContainer: 'swal2-html-container',
          confirmButton: 'swal2-confirm',
        },
      })
    } finally {
      dispatch(resetUploadState())
      document.querySelector('.progress').style.display = 'none'
      document.getElementById('btn-upload').style.display = 'inline-block'
      document.getElementById('btn-backup').style.display = 'inline-block'
      // Reset the file input
      e.target.value = ''
    }
  }

  return (
    <>
      <div className="row py-2 mt-4">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>Backup</b>
          </h4>
        </div>
      </div>
      <div className="row content px-3">
        <div id="backup-section" className="col-12 my-3">
          <div className="text-right">
            <input
              name="backup_upload"
              style={{ display: 'none' }}
              type="file"
              onChange={handleFileUpload}
            />
            <button
              id="btn-backup"
              className="btn btn-long btn-outline-primary mr-2"
              onClick={handleBackup}
              disabled={isUploading}
            >
              Get Backup
            </button>
            <button
              id="btn-upload"
              className="btn btn-primary"
              type="button"
              onClick={handleUpload}
              disabled={isUploading}
            >
              {isUploading ? 'Uploading...' : 'Upload and Recover'}
            </button>
          </div>
          <div
            className="progress-bar progress-bar-striped progress active w-100"
            style={{ display: isUploading ? 'block' : 'none' }}
          >
            <div className="bar" style={{ width: `${uploadProgress}%` }}></div>
          </div>
        </div>
      </div>
    </>
  )
}
