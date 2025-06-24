import React from 'react';
import classNames from 'classnames';

/**
 * File upload tab component for the asset modal
 * @param {Object} props - Component props
 * @param {Object} props.fileInputRef - Reference to the file input
 * @param {Object} props.dropZoneRef - Reference to the drop zone
 * @param {Function} props.handleFileSelect - File select handler
 * @param {Function} props.handleFileDrop - File drop handler
 * @param {Function} props.handleDragOver - Drag over handler
 * @param {Function} props.handleDragEnter - Drag enter handler
 * @param {Function} props.handleDragLeave - Drag leave handler
 * @param {boolean} props.isSubmitting - Whether the form is submitting
 * @param {number} props.uploadProgress - Upload progress
 * @returns {JSX.Element} - File upload tab component
 */
export const FileUploadTab = ({
  fileInputRef,
  dropZoneRef,
  handleFileSelect,
  handleFileDrop,
  handleDragOver,
  handleDragEnter,
  handleDragLeave,
  isSubmitting,
  uploadProgress,
}) => {
  return (
    <div
      id="tab-file_upload"
      className={classNames('tab-pane', {
        active: true,
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
              disabled={isSubmitting}
            >
              Add Files
            </button>
            <input
              ref={fileInputRef}
              name="file_upload"
              type="file"
              style={{ display: 'none' }}
              onChange={handleFileSelect}
              disabled={isSubmitting}
            />
            <br />
            or
          </div>
          <div>drop files here to upload</div>
        </div>
      </div>
      <div
        className="progress active"
        style={{
          marginTop: '1.25rem',
          opacity: isSubmitting ? 1 : 0,
          maxHeight: isSubmitting ? '20px' : '0',
          overflow: 'hidden',
          transition: 'opacity 0.3s ease-in-out, max-height 0.3s ease-in-out',
        }}
      >
        <div
          className="bar progress-bar-striped progress-bar progress-bar-animated"
          style={{
            width: `${uploadProgress}%`,
            transition: 'width 0.3s ease-in-out',
          }}
        ></div>
      </div>
    </div>
  );
};
