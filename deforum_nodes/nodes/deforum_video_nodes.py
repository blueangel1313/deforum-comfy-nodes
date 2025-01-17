import os

import cv2
import imageio
import numpy as np
import torch
from tqdm import tqdm

import folder_paths
from ..modules.deforum_comfyui_helpers import tensor2pil, pil2tensor, find_next_index, pil_image_to_base64, tensor_to_webp_base64

video_extensions = ['webm', 'mp4', 'mkv', 'gif']

import moviepy.editor as mp
from scipy.io.wavfile import write
import tempfile


def save_to_file(data, filepath: str):
    # Ensure the audio data is reshaped properly for mono/stereo
    if data.num_channels > 1:
        audio_data_reshaped = data.audio_data.reshape((-1, data.num_channels))
    else:
        audio_data_reshaped = data.audio_data
    write(filepath, data.sample_rate, audio_data_reshaped.astype(np.int16))
    return True

class DeforumLoadVideo:

    def __init__(self):
        self.video_path = None

    @classmethod
    def INPUT_TYPES(s):
        input_dir = folder_paths.get_input_directory()
        files = []
        for f in os.listdir(input_dir):
            if os.path.isfile(os.path.join(input_dir, f)):
                file_parts = f.split('.')
                if len(file_parts) > 1 and (file_parts[-1] in video_extensions):
                    files.append(f)
        return {"required": {
                    "video": (sorted(files),),
                    "reset": ("BOOLEAN", {"default": False},),

        },}

    CATEGORY = "deforum/video"
    display_name = "Load Video"

    RETURN_TYPES = ("IMAGE","INT","INT")
    RETURN_NAMES = ("IMAGE","FRAME_IDX","MAX_FRAMES")
    FUNCTION = "load_video_frame"

    def __init__(self):
        self.cap = None
        self.current_frame = None

    def load_video_frame(self, video, reset):
        video_path = folder_paths.get_annotated_filepath(video)

        # Initialize or reset video capture
        if self.cap is None or self.cap.get(cv2.CAP_PROP_POS_FRAMES) >= self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or self.video_path != video_path or reset:
            try:
                self.cap.release()
            except:
                pass
            self.cap = cv2.VideoCapture(video_path)

            self.cap = cv2.VideoCapture(video_path)
            self.current_frame = -1
            self.video_path = video_path



        success, frame = self.cap.read()
        if success:
            self.current_frame += 1
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = np.array(frame).astype(np.float32)
            frame = pil2tensor(frame)  # Convert to torch tensor
        else:
            # Reset if reached the end of the video
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            success, frame = self.cap.read()
            self.current_frame = 0
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = np.array(frame).astype(np.float32)
            frame = pil2tensor(frame)  # Convert to torch tensor

        return (frame,self.current_frame,self.cap.get(cv2.CAP_PROP_POS_FRAMES),)

    @classmethod
    def IS_CHANGED(cls, text, autorefresh):
        # Force re-evaluation of the node
        if autorefresh == "Yes":
            return float("NaN")

    @classmethod
    def VALIDATE_INPUTS(cls, video):
        if not folder_paths.exists_annotated_filepath(video):
            return "Invalid video file: {}".format(video)
        return True

class DeforumVideoSaveNode:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.images = []
        self.size = None
    @classmethod
    def INPUT_TYPES(s):
        return {"required":
                    {"image": ("IMAGE",),
                     "filename_prefix": ("STRING",{"default":"Deforum"}),
                     "fps": ("INT", {"default": 24, "min": 1, "max": 10000},),
                     "codec": (["libx265", "libx264", "libvpx-vp9", "libaom-av1", "mpeg4", "libvpx"],),
                     "pixel_format": (["yuv420p", "yuv422p", "yuv444p", "yuvj420p", "yuvj422p", "yuvj444p", "rgb24", "rgba", "nv12", "nv21"],),
                     "format": (["mp4", "mov", "gif", "avi"],),
                     "quality": ("INT", {"default": 10, "min": 1, "max": 10},),
                     "dump_by": (["max_frames", "per_N_frames"],),
                     "dump_every": ("INT", {"default": 0, "min": 0, "max": 4096},),
                     "dump_now": ("BOOLEAN", {"default": False},),
                     "skip_save": ("BOOLEAN", {"default": False},),
                     "skip_return": ("BOOLEAN", {"default": True},),
                     "enable_preview": ("BOOLEAN", {"default": True},),
                     },
                "optional": {
                    "deforum_frame_data": ("DEFORUM_FRAME_DATA",),
                    "audio": ("AUDIO",),
                }

                }

    RETURN_TYPES = ("IMAGE",)
    OUTPUT_NODE = True

    FUNCTION = "fn"
    display_name = "Save Video"
    CATEGORY = "deforum/video"
    def add_image(self, image):
        self.images.append(image)

    def fn(self,
           image,
           filename_prefix,
           fps,
           codec,
           pixel_format,
           format,
           quality,
           dump_by,
           dump_every,
           dump_now,
           skip_save,
           skip_return,
           enable_preview,
           deforum_frame_data={},
           audio=None):
        dump = False
        ret = "skip"
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
            filename_prefix, self.output_dir)
        counter = find_next_index(full_output_folder, filename_prefix, format)
        anim_args = deforum_frame_data.get("anim_args")

        if image is not None:

            if anim_args is not None:
                max_frames = anim_args.max_frames
            else:
                max_frames = image.shape[0] + len(self.images) + 2
            if not deforum_frame_data.get("reset", None):
                if image.shape[0] > 1:
                    for img in image:
                        self.add_image(img)
                else:
                    self.add_image(image[0])
            print(f"[deforum] Video Save node cached {len(self.images)} frames")
            # When the current frame index reaches the last frame, save the video

            if dump_by == "max_frames":
                dump = len(self.images) >= max_frames
            else:
                dump = len(self.images) >= dump_every
            if deforum_frame_data.get("reset", None):
                dump = True
            ret = "skip"
            if dump or dump_now:  # frame_idx is 0-based
                if len(self.images) >= 2:
                    if not skip_save:
                        self.save_video(full_output_folder, filename, counter, fps, audio, codec, format)
                    if not skip_return:
                        ret = torch.stack([pil2tensor(i)[0] for i in self.images], dim=0)
                self.images = []  # Empty the list for next use

            if deforum_frame_data.get("reset", None):
                if image.shape[0] > 1:
                    for img in image:
                        self.add_image(img)
                else:
                    self.add_image(image[0])
        if enable_preview and image is not None:

            ui_ret = {"counter":(len(self.images),),
                      "should_dump":(dump or dump_now,),
                      "frames":([tensor_to_webp_base64(i) for i in image]),
                      "fps":(fps,)}
        else:

            if anim_args is not None:
                max_frames = anim_args.max_frames
            else:
                max_frames = len(self.images) + 5
            if dump_by == "max_frames":
                dump = len(self.images) >= max_frames
            else:
                dump = len(self.images) >= dump_every
            if deforum_frame_data.get("reset", None):
                dump = True
            if dump or dump_now:  # frame_idx is 0-based
                if len(self.images) >= 2:
                    if not skip_save:
                        self.save_video(full_output_folder, filename, counter, fps, audio, codec, format)
                    if not skip_return:
                        ret = torch.stack([pil2tensor(i)[0] for i in self.images], dim=0)
                self.images = []
            ui_ret = {"counter":(len(self.images),),
                      "should_dump":(dump or dump_now,),
                      "frames":([]),
                      "fps":(fps,)}

        return {"ui": ui_ret, "result": (ret,)}
    def save_video(self, full_output_folder, filename, counter, fps, audio, codec, ext):
        output_path = os.path.join(full_output_folder, f"{filename}_{counter}.{ext}")

        print("[deforum] Saving video:", output_path)

        # writer = imageio.get_writer(output_path, fps=fps, codec=codec, quality=quality, pixelformat=pixel_format, format=format)
        # for frame in tqdm(self.images, desc=f"Saving {format} (imageio)"):
        #     writer.append_data(np.clip(255. * frame.detach().cpu().numpy().squeeze(), 0, 255).astype(np.uint8))
        # writer.close()
        video_clip = mp.ImageSequenceClip(
            [np.clip(255. * frame.detach().cpu().numpy().squeeze(), 0, 255).astype(np.uint8) for frame in
             self.images], fps=fps)

        if audio is not None:
            # Generate a temporary file for the audio
            with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp_audio_file:
                save_to_file(audio, tmp_audio_file.name)
                # Load the audio clip
                audio_clip = mp.AudioFileClip(tmp_audio_file.name)
                # Calculate video duration
                video_duration = len(self.images) / fps
                # Trim or loop the audio clip to match the video length
                if audio_clip.duration > video_duration:
                    audio_clip = audio_clip.subclip(0,
                                                    video_duration)  # Trim the audio to match video length
                elif audio_clip.duration < video_duration:
                    # If you want to loop the audio, uncomment the following line
                    # audio_clip = audio_clip.loop(duration=video_duration)
                    pass  # If you prefer silence after the audio ends, do nothing
                # Set the audio on the video clip
                video_clip = video_clip.set_audio(audio_clip)

        video_clip.write_videofile(output_path, codec=codec, audio_codec='aac')

    @classmethod
    def IS_CHANGED(s, text, autorefresh):
        # Force re-evaluation of the node
        if autorefresh == "Yes":
            return float("NaN")

