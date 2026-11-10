"""Contact sheet of rendered film frames, run inside Blender (for its numpy and image loading).

    BLENDER -b --python tools/dev/montage.py -- OUT.png FRAME.png [FRAME.png ...]

Edit k (scale) and cols at the top of the body as needed.
"""
import bpy, numpy as np, sys
args = sys.argv[sys.argv.index("--")+1:]
out, files = args[0], args[1:]
imgs=[]
for f in files:
    im=bpy.data.images.load(f); w,h=im.size; px=np.empty(w*h*4,np.float32); im.pixels.foreach_get(px); imgs.append(px.reshape(h,w,4)); bpy.data.images.remove(im)
h,w=imgs[0].shape[:2]; k=1; cols=6; rows=(len(imgs)+cols-1)//cols; g=6
sheet=np.full((rows*(h*k+g), cols*(w*k+g), 4), 0.15, np.float32); sheet[...,3]=1
for i,im in enumerate(imgs):
    r,c=i//cols,i%cols
    big=np.repeat(np.repeat(im,k,0),k,1)
    # blender rows are bottom-up: place counting from the top
    y0=(rows-1-r)*(h*k+g); x0=c*(w*k+g)
    sheet[y0:y0+h*k, x0:x0+w*k]=big
H,W=sheet.shape[:2]
o=bpy.data.images.new("sheet",W,H); o.pixels.foreach_set(sheet.ravel()); o.filepath_raw=out; o.file_format='PNG'; o.save()
