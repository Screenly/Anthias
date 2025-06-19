import { useDispatch } from 'react-redux'
import Swal from 'sweetalert2'

import { SWEETALERT_TIMER } from '@/constants'
import { systemOperation } from '@/store/settings'

export const SystemControls = () => {
  const dispatch = useDispatch()

  const handleSystemOperation = async (operation) => {
    const config = {
      reboot: {
        title: 'Are you sure?',
        text: 'Are you sure you want to reboot your device?',
        confirmButtonText: 'Reboot',
        endpoint: '/api/v2/reboot',
        successMessage: 'Reboot has started successfully.',
        errorMessage: 'Failed to reboot device',
      },
      shutdown: {
        title: 'Are you sure?',
        text: 'Are you sure you want to shutdown your device?',
        confirmButtonText: 'Shutdown',
        endpoint: '/api/v2/shutdown',
        successMessage:
          'Device shutdown has started successfully.\nSoon you will be able to unplug the power from your Raspberry Pi.',
        errorMessage: 'Failed to shutdown device',
      },
    }

    const { title, text, confirmButtonText, endpoint, successMessage } =
      config[operation]

    const result = await Swal.fire({
      title,
      text,
      icon: 'warning',
      showCancelButton: true,
      confirmButtonText,
      cancelButtonText: 'Cancel',
      reverseButtons: true,
      cancelButtonColor: '#6c757d',
      customClass: {
        popup: 'swal2-popup',
        title: 'swal2-title',
        htmlContainer: 'swal2-html-container',
        confirmButton: 'swal2-confirm',
        cancelButton: 'swal2-cancel',
        actions: 'swal2-actions',
      },
    })

    if (result.isConfirmed) {
      try {
        await dispatch(
          systemOperation({ operation, endpoint, successMessage }),
        ).unwrap()

        await Swal.fire({
          title: 'Success!',
          text: successMessage,
          icon: 'success',
          timer: SWEETALERT_TIMER,
          showConfirmButton: false,
          customClass: {
            popup: 'swal2-popup',
            title: 'swal2-title',
            htmlContainer: 'swal2-html-container',
          },
        })
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
      }
    }
  }

  const handleReboot = () => handleSystemOperation('reboot')
  const handleShutdown = () => handleSystemOperation('shutdown')

  return (
    <>
      <div className="row py-2 mt-4">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>System Controls</b>
          </h4>
        </div>
      </div>
      <div className="row content px-3">
        <div className="col-12 my-3">
          <div className="text-right">
            <button
              className="btn btn-danger btn-long mr-2"
              type="button"
              onClick={handleReboot}
            >
              Reboot
            </button>
            <button
              className="btn btn-danger btn-long"
              type="button"
              onClick={handleShutdown}
            >
              Shutdown
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
