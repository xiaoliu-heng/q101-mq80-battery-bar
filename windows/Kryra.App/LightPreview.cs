using System;
using System.Diagnostics;
using System.Windows;
using System.Windows.Media;
using Kryra.Core;

namespace Kryra.Windows;

public sealed class LightPreview:FrameworkElement
{
    public string Mode {get;set;}="rainbow";
    public Rgb Color {get;set;}=new(52,211,153);
    public int Brightness {get;set;}=25;
    public int PeriodMs {get;set;}=5000;
    public double PhaseOffset {get;set;}
    public long Start {get;set;}=Stopwatch.GetTimestamp();
    protected override void OnRender(DrawingContext dc)
    {
        var w=ActualWidth; var h=ActualHeight; if(w<=0||h<=0)return;
        var elapsed=Math.Max(0,(double)(Stopwatch.GetTimestamp()-Start)/Stopwatch.Frequency+PhaseOffset);
        if(!SystemParameters.ClientAreaAnimation) elapsed=0;
        var phase=elapsed*1000/Math.Max(500,PeriodMs)%1;
        var intensity=Brightness/100d;
        if(Mode=="blink") intensity*=phase<.5?1:0;
        if(Mode=="breathe") { var triangle=1-Math.Abs(phase*2-1); intensity*=triangle*triangle; }
        var outer=new Rect(3,1,w-6,h-2);
        dc.DrawRoundedRectangle(new SolidColorBrush(System.Windows.Media.Color.FromArgb(90,255,255,255)),new Pen(new SolidColorBrush(System.Windows.Media.Color.FromArgb(170,255,255,255)),1),outer,(w-6)/2,(w-6)/2);
        var bar=new Rect(w/2-11,15,22,h-30); var stops=new GradientStopCollection();
        for(var i=0;i<9;i++)
        {
            var rgb=Mode=="rainbow"?Rgb.Rainbow(i/9d+phase):Color;
            var display=Math.Sqrt(Math.Clamp(intensity,0,1));
            stops.Add(new GradientStop(System.Windows.Media.Color.FromArgb((byte)(display*255),rgb.R,rgb.G,rgb.B),i/8d));
        }
        var brush=new LinearGradientBrush(stops,new Point(0,0),new Point(0,1));
        for(var blur=9;blur>0;blur-=3) { var glow=brush.Clone(); glow.Opacity=.035; dc.DrawRoundedRectangle(glow,null,new Rect(bar.X-blur,bar.Y-blur,bar.Width+2*blur,bar.Height+2*blur),18,18); }
        dc.DrawRoundedRectangle(new SolidColorBrush(System.Windows.Media.Color.FromArgb(35,20,45,35)),null,bar,11,11);
        dc.DrawRoundedRectangle(brush,new Pen(new SolidColorBrush(System.Windows.Media.Color.FromArgb(95,255,255,255)),1),bar,11,11);
        dc.DrawRoundedRectangle(new SolidColorBrush(System.Windows.Media.Color.FromArgb(95,255,255,255)),null,new Rect(bar.X+4,bar.Y+5,2,bar.Height-10),1,1);
    }
}
