donor image needs to have the piping material around its seams replaced with the piping material in the color reference folder
you will need to convert the donor image to a 4k png and store it in the processed donor image folder because the apis wont accept tiffs
you will need to convert the color reference images to 4k pngs from tiffs also
keep the images and the subkects in the new color and dnor pngs images  like they arein the reference images. they should have same forms, image proprotions cropping and everything else the tiffs and the subjects in the tiffs had
youll need to supbmit the fies to the image api along with a prompt
create the prompt that explains what the image s purposes are and what to do with them.. th eprompt should be in the prompts folder and can just be called initial_prompt_PID-1.md
the prompt shoul drequest tpo thave th epiping on the furniture material swapped out with the new color only. and a new 4k image should be returned cropped witht he same proportio ns as the donor image tiff had.
the result image should be in adobe RGB 1998 format.
if the --results flag was set return as many images as were requested as lonly as the number is less than 100
place the result images in the output folder in a a subfolder named aftert he original product image

before coding any of this make suggestions on better ways to go about the process if you can think of any

make sure to include detailed console logging of what you are submitting to the api, what files names are and process steps you are taking

if anytghing is unclear about how to implement the code, stop and ask me for clarification before porceeding

I put dummy files in the some of the folders where files are going to be saved to help act as a guideline for you how to implement the code
