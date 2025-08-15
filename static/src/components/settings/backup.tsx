import { useDispatch, useSelector } from 'react-redux'
import Swal from 'sweetalert2'
import { RootState, AppDispatch } from '@/types'

import { SWEETALERT_TIMER } from '@/constants'
import {
  createBackup,
  uploadBackup,
  resetUploadState,
  fetchSettings,
} from '@/store/settings'

export const Backup = () => {
  const dispatch = useDispatch<AppDispatch>()
  const { isUploading, uploadProgress } = useSelector(
    (state: RootState) => state.settings,
  )

  const handleBackup = async () => {
    const backupButton = document.getElementById(
      'btn-backup',
    ) as HTMLButtonElement | null
    const uploadButton = document.getElementById(
      'btn-upload',
    ) as HTMLButtonElement | null

    if (!backupButton || !uploadButton) return

    const originalText = backupButton.textContent
    backupButton.textContent = 'Preparing archive...'
    backupButton.disabled = true
    uploadButton.disabled = true

    try {
      const result = await dispatch(createBackup()).unwrap()
      if (result) {
        window.location.href = `/static_with_mime/${result}?mime=application/x-tgz`
      }
    } catch (err) {
      await Swal.fire({
        title: 'Error!',
        text:
          (err as Error).message ||
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
      if (backupButton) {
        backupButton.textContent = originalText
        backupButton.disabled = false
      }
      if (uploadButton) {
        uploadButton.disabled = false
      }
    }
  }

  const handleUpload = (e: React.MouseEvent) => {
    e.preventDefault()
    const fileInput = document.querySelector(
      '[name="backup_upload"]',
    ) as HTMLInputElement | null
    if (fileInput) {
      fileInput.value = '' // Reset the file input
      fileInput.click()
    }
  }

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    const uploadButton = document.getElementById(
      'btn-upload',
    ) as HTMLElement | null
    const backupButton = document.getElementById(
      'btn-backup',
    ) as HTMLElement | null
    const progressElement = document.querySelector(
      '.progress',
    ) as HTMLElement | null

    if (uploadButton) uploadButton.style.display = 'none'
    if (backupButton) backupButton.style.display = 'none'
    if (progressElement) progressElement.style.display = 'block'

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
          (err as Error).message ||
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
      if (progressElement) progressElement.style.display = 'none'
      if (uploadButton) uploadButton.style.display = 'inline-block'
      if (backupButton) backupButton.style.display = 'inline-block'
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
          <div className="text-end">
            <input
              name="backup_upload"
              style={{ display: 'none' }}
              type="file"
              onChange={handleFileUpload}
            />
            <button
              id="btn-backup"
              className="btn btn-long btn-outline-primary me-2"
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
