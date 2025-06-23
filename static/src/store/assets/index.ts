import assetsReducer from '@/store/assets/assets-list-slice';
import { addAsset } from '@/store/assets/assets-list-slice';
import {
  fetchAssets,
  updateAssetOrder,
  toggleAssetEnabled,
} from '@/store/assets/assets-thunks';
import {
  selectActiveAssets,
  selectInactiveAssets,
} from '@/store/assets/assets-selectors';
import assetModalReducer from './asset-modal-slice';
import {
  uploadFile,
  saveAsset,
  setActiveTab,
  updateFormData,
  setValid,
  setErrorMessage,
  setStatusMessage,
  setUploadProgress,
  resetForm,
  validateUrl,
  selectAssetModalState,
} from './asset-modal-slice';

export {
  assetsReducer,
  addAsset,
  fetchAssets,
  updateAssetOrder,
  toggleAssetEnabled,
  selectActiveAssets,
  selectInactiveAssets,
  // Asset Modal exports
  assetModalReducer,
  uploadFile,
  saveAsset,
  setActiveTab,
  updateFormData,
  setValid,
  setErrorMessage,
  setStatusMessage,
  setUploadProgress,
  resetForm,
  validateUrl,
  selectAssetModalState,
};
