import React from 'react';
import classNames from 'classnames';
import { FormData } from '@/types';

interface UriTabProps {
  formData: FormData;
  isValid: boolean;
  errorMessage: string;
  isSubmitting: boolean;
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}

export const UriTab = ({
  formData,
  isValid,
  errorMessage,
  isSubmitting,
  handleInputChange,
}: UriTabProps) => {
  return (
    <div
      id="tab-uri"
      className={classNames('tab-pane', {
        active: true,
      })}
    >
      <div className="form-group row uri">
        <label className="col-4 col-form-label">Asset URL</label>
        <div className="col-7 controls">
          <input
            className={classNames('form-control', 'shadow-none', {
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
            <div className="invalid-feedback">{errorMessage}</div>
          )}
        </div>
      </div>
      <div className="form-group row skip_asset_check_checkbox">
        <label className="col-4 small">Skip asset check</label>
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
  );
};
