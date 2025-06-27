import { FaImage, FaVideo, FaGlobe } from 'react-icons/fa';

interface MimetypeIconProps {
  mimetype: string;
  className: string;
  style: React.CSSProperties;
}

export const MimetypeIcon = ({
  mimetype,
  className,
  style,
}: MimetypeIconProps) => {
  return {
    image: <FaImage className={className} style={style} />,
    video: <FaVideo className={className} style={style} />,
    webpage: <FaGlobe className={className} style={style} />,
    default: <FaGlobe className={className} style={style} />,
  }[mimetype];
};
