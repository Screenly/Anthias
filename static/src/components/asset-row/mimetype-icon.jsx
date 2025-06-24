import { FaImage, FaVideo, FaGlobe } from 'react-icons/fa'

export const MimetypeIcon = ({ mimetype, className, style }) => {
  return {
    image: <FaImage className={className} style={style} />,
    video: <FaVideo className={className} style={style} />,
    webpage: <FaGlobe className={className} style={style} />,
    default: <FaGlobe className={className} style={style} />,
  }[mimetype]
}
